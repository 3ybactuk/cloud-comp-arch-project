#!/usr/bin/env python3
"""Part 3a screening: deploy memcached + parsec waves, mcperf scan, gate on ~30K QPS / p95."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLUSTER_NAME = "part3.k8s.local"
ZONE = "europe-west1-b"
GCP_PROJECT = os.environ.get("GCP_PROJECT", "").strip() or "cca-eth-2026-group-087"
_KUBECTL_CONTEXT: str | None = None
POLL_INTERVAL_SEC = 10
JOB_TIMEOUT_SEC = 60 * 60
MCPERF_REMOTE_ABSPATH = "/home/ubuntu/memcache-perf-dynamic"
MCPERF_REMOTE_OUT = "/tmp/mcperf_part3_measure.txt"

REQUIRED_PART3_NODE_TYPES = [
    "node-a-8core",
    "node-b-4core",
    "client-agent-a",
    "client-agent-b",
    "client-measure",
]

PARSEC_META = {
    "barnes": ("anakli/cca:splash2x_barnes", "splash2x"),
    "radix": ("anakli/cca:splash2x_radix", "splash2x"),
    "blackscholes": ("anakli/cca:parsec_blackscholes", "parsec"),
    "canneal": ("anakli/cca:parsec_canneal", "parsec"),
    "freqmine": ("anakli/cca:parsec_freqmine", "parsec"),
    "streamcluster": ("anakli/cca:parsec_streamcluster", "parsec"),
    "vips": ("anakli/cca:parsec_vips", "parsec"),
}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def run_cmd(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def kubectl(args: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["kubectl"]
    if _KUBECTL_CONTEXT:
        cmd += ["--context", _KUBECTL_CONTEXT]
    cmd += args
    return run_cmd(cmd, check=check, capture=capture)


def _argv_ssh_direct(external_ip: str, remote_command: str, *, via_stdin: bool = False) -> list[str]:
    key = os.path.expanduser("~/.ssh/cloud-computing")
    base = [
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30",
        "-i", key, f"ubuntu@{external_ip.strip()}",
    ]
    return base + (["bash"] if via_stdin else [remote_command])


def _argv_gcloud_ssh(k8s_node_name: str, remote_command: str) -> list[str]:
    return [
        "gcloud", "compute", "ssh", f"ubuntu@{k8s_node_name}",
        "--zone", ZONE,
        "--ssh-key-file", os.path.expanduser("~/.ssh/cloud-computing"),
        "--project", GCP_PROJECT,
        "--command", remote_command,
    ]


def vm_ssh(
    k8s_name: str,
    external_ip: str | None,
    remote_command: str,
    check: bool = True,
    *,
    mute_ssh_fail_hints: bool = False,
    via_stdin: bool = False,
) -> subprocess.CompletedProcess:
    attempts: list[tuple[str, list[str]]] = []
    if external_ip and external_ip.strip():
        attempts.append(("ssh", _argv_ssh_direct(external_ip, remote_command, via_stdin=via_stdin)))
    attempts.append(("gcloud", _argv_gcloud_ssh(k8s_name, remote_command)))

    proc_final: subprocess.CompletedProcess | None = None
    for label, argv in attempts:
        log(f"$ [{label}] {' '.join(argv)}")
        if via_stdin and label == "ssh":
            proc = subprocess.run(argv, input=remote_command, capture_output=True, text=True)
        else:
            proc = subprocess.run(argv, capture_output=True, text=True)

        if proc.returncode != 0:
            blob = ((proc.stderr or "") + "\n" + (proc.stdout or "")).replace("\n", " ").strip()
            if mute_ssh_fail_hints:
                log(f"[{label}] {k8s_name}: rc={proc.returncode}" + (f" — {blob[:200]}" if blob else ""))
            else:
                err = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
                if err:
                    log(f"[{label}] {k8s_name}: failed rc={proc.returncode}\n{err[:3500]}")

        proc_final = proc
        if proc.returncode == 0:
            if label != attempts[0][0]:
                log(f"SSH to {k8s_name}: fallback {label}")
            break

    assert proc_final is not None
    if check:
        proc_final.check_returncode()
    return proc_final


def node_external_ip(node: dict[str, str]) -> str | None:
    ip = (node.get("external_ip") or "").strip()
    return ip or None


def write_manifest_and_apply(manifest: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(manifest)
        tmp = f.name
    try:
        kubectl(["apply", "-f", tmp])
    finally:
        Path(tmp).unlink(missing_ok=True)


def delete_all_workloads() -> None:
    kubectl(["delete", "jobs", "--all"], check=False)
    kubectl(["delete", "pods", "--all"], check=False)
    kubectl(["delete", "svc", "some-memcached-11211"], check=False)
    time.sleep(8)


def debug_cluster_topology() -> str:
    lines: list[str] = []
    if _KUBECTL_CONTEXT:
        lines.append(f"kubectl uses --context {_KUBECTL_CONTEXT!r}")
    cur = run_cmd(["kubectl", "config", "current-context"], capture=True, check=False)
    lines.append(f"current-context: {(cur.stdout or '').strip() or '(empty)'}")
    nodes = kubectl(["get", "nodes", "-o", "json"], capture=True, check=False)
    if nodes.returncode != 0 or not (nodes.stdout or "").strip():
        lines.append((nodes.stderr or "kubectl get nodes failed").strip())
        return "\n".join(lines)
    data = json.loads(nodes.stdout)
    lines.append(f"nodes ({len(data.get('items', []))}):")
    for item in data.get("items", []):
        name = item["metadata"]["name"]
        nt = item["metadata"].get("labels", {}).get("cca-project-nodetype")
        lines.append(f"  {name}  cca-project-nodetype={nt!r}")
        if nt is None:
            lines[-1] += "  (missing label — wrong cluster?)"
    lines.append("")
    lines.append(
        "Fix: kubectl config use-context … or kops export kubeconfig --admin --name part3.k8s.local\n"
        "Then: python3 run_part3a.py --kubectl-context '…'"
    )
    return "\n".join(lines)


def preflight_kubectl_part3_cluster() -> None:
    nodes = discover_nodes()
    missing = [n for n in REQUIRED_PART3_NODE_TYPES if n not in nodes]
    if missing:
        print("\n*** preflight failed ***\n", file=sys.stderr)
        print(debug_cluster_topology(), file=sys.stderr)
        print(f"\nMissing cca-project-nodetype: {missing}\n", file=sys.stderr)
        sys.exit(2)
    log("preflight OK")


def discover_nodes() -> dict[str, dict[str, str]]:
    data = json.loads(kubectl(["get", "nodes", "-o", "json"], capture=True).stdout)
    by_label: dict[str, dict[str, str]] = {}
    for item in data["items"]:
        labels = item["metadata"].get("labels", {})
        nodetype = labels.get("cca-project-nodetype")
        if not nodetype:
            continue
        internal_ip = external_ip = ""
        for addr in item["status"].get("addresses", []):
            if addr.get("type") == "InternalIP":
                internal_ip = addr.get("address", "")
            if addr.get("type") == "ExternalIP":
                external_ip = addr.get("address", "")
        by_label[nodetype] = {"name": item["metadata"]["name"], "internal_ip": internal_ip, "external_ip": external_ip}
    return by_label


def memcached_manifest(mem_threads: int, node_label: str) -> str:
    # no taskset here — pinning belongs in policy if needed
    return f"""apiVersion: v1
kind: Pod
metadata:
  name: some-memcached
  labels:
    name: some-memcached
spec:
  containers:
  - image: anakli/memcached:t1
    name: memcached
    imagePullPolicy: Always
    command: ["/bin/sh"]
    args: ["-c", "/memcached/memcached -t {mem_threads} -u memcache"]
    resources:
      requests:
        cpu: "{mem_threads}000m"
        memory: "512Mi"
      limits:
        memory: "1Gi"
  nodeSelector:
    cca-project-nodetype: "{node_label}"
---
apiVersion: v1
kind: Service
metadata:
  name: some-memcached-11211
spec:
  type: NodePort
  selector:
    name: some-memcached
  ports:
  - protocol: TCP
    port: 11211
    targetPort: 11211
    nodePort: 31211
"""


def parsec_job_manifest(job: str, node_label: str, threads: int) -> str:
    image, suite = PARSEC_META[job]
    return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: parsec-{job}
  labels:
    app: parsec-{job}
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: parsec-{job}
    spec:
      restartPolicy: Never
      nodeSelector:
        cca-project-nodetype: "{node_label}"
      containers:
      - image: {image}
        name: parsec-{job}
        imagePullPolicy: Always
        command: ["/bin/sh"]
        args: ["-c", "./run -a run -S {suite} -p {job} -i native -n {threads}"]
        resources:
          requests:
            cpu: "{threads * 900}m"
            memory: "256Mi"
"""


def wait_for_pod_ready(name: str, timeout_sec: int = 300) -> None:
    start = time.time()
    while time.time() - start <= timeout_sec:
        phase = kubectl(
            ["get", "pod", name, "-o", "jsonpath={.status.phase}"],
            check=False, capture=True,
        ).stdout.strip()
        if phase == "Running":
            log(f"{name} Running")
            return
        time.sleep(3)
    raise RuntimeError(f"pod {name} not Ready")


def get_memcached_nodeport_addr(timeout_sec: int = 120) -> str:
    """client-measure is off-cluster; use node InternalIP:31211."""
    start = time.time()
    while time.time() - start <= timeout_sec:
        node_name = kubectl(
            ["get", "pod", "some-memcached", "-o", "jsonpath={.spec.nodeName}"],
            capture=True, check=False,
        ).stdout.strip()
        if node_name:
            ip = kubectl(
                [
                    "get", "node", node_name,
                    "-o", 'jsonpath={.status.addresses[?(@.type=="InternalIP")].address}',
                ],
                capture=True, check=False,
            ).stdout.strip()
            if ip:
                addr = f"{ip}:31211"
                log(f"memcached @ {addr} ({node_name})")
                return addr
        time.sleep(3)
    raise RuntimeError("no memcached node IP")


def wait_for_jobs(job_names: list[str]) -> bool:
    start = time.time()
    pending = set(job_names)
    while pending:
        if time.time() - start > JOB_TIMEOUT_SEC:
            log("job wait timeout")
            return False
        done = []
        for name in list(pending):
            status = kubectl(
                ["get", "job", name, "-o", "jsonpath={.status.conditions[*].type}"],
                check=False, capture=True,
            ).stdout.strip()
            if "Complete" in status:
                done.append(name)
            elif "Failed" in status:
                log(f"job failed: {name}")
                return False
        for d in done:
            pending.remove(d)
            log(f"done: {d}")
        if pending:
            log(f"still: {', '.join(sorted(pending))}")
            time.sleep(POLL_INTERVAL_SEC)
    return True


def run_remote_mcperf_clients(node_map: dict[str, dict[str, str]], mem_ip: str) -> None:
    na, nb, nm = node_map["client-agent-a"], node_map["client-agent-b"], node_map["client-measure"]
    agent_a_ip, agent_b_ip = na["internal_ip"], nb["internal_ip"]

    for role_node in (na, nb, nm):
        vm_ssh(
            role_node["name"], node_external_ip(role_node),
            "pkill -f mcperf 2>/dev/null; sleep 1; exit 0",
            check=False, mute_ssh_fail_hints=True, via_stdin=True,
        )
    time.sleep(2)

    log("mcperf agent A")
    vm_ssh(
        na["name"], node_external_ip(na),
        f"bash -c 'setsid {MCPERF_REMOTE_ABSPATH}/mcperf -T 2 -A >/tmp/mcperf_agent_a.log 2>&1 </dev/null & disown' "
        "&& sleep 1 && pgrep -a mcperf",
    )
    time.sleep(1)

    log("mcperf agent B")
    vm_ssh(
        nb["name"], node_external_ip(nb),
        f"bash -c 'setsid {MCPERF_REMOTE_ABSPATH}/mcperf -T 4 -A >/tmp/mcperf_agent_b.log 2>&1 </dev/null & disown' "
        "&& sleep 1 && pgrep -a mcperf",
    )
    time.sleep(2)

    log("loadonly")
    vm_ssh(
        nm["name"], node_external_ip(nm),
        f"{MCPERF_REMOTE_ABSPATH}/mcperf -s {mem_ip} --loadonly >/tmp/mcperf_loadonly.log 2>&1; exit 0",
    )

    # loadonly separate from measure; stdbuf helps last lines hit disk before pkill
    log("measure (bg)")
    measure_cmd = (
        f"bash -c 'setsid stdbuf -oL {MCPERF_REMOTE_ABSPATH}/mcperf -s {mem_ip} "
        f"-a {agent_a_ip} -a {agent_b_ip} "
        "--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 "
        "--scan 30000:30500:5 "
        f">{MCPERF_REMOTE_OUT} 2>&1 </dev/null & disown'"
    )
    vm_ssh(nm["name"], node_external_ip(nm), measure_cmd)
    time.sleep(6)


def fetch_remote_file(node_meta: dict[str, str], remote_path: str, local_path: Path, retries: int = 5) -> None:
    content = ""
    for attempt in range(retries):
        res = vm_ssh(
            node_meta["name"], node_external_ip(node_meta),
            f"cat {remote_path} 2>/dev/null || echo ''",
            check=False,
        )
        content = res.stdout or ""
        if content.strip():
            local_path.write_text(content)
            log(f"fetched {remote_path} ({len(content)} B)")
            return
        if attempt < retries - 1:
            log(f"{remote_path} empty, sleeping 5s ({attempt + 1}/{retries})")
            time.sleep(5)
    local_path.write_text(content)
    log(f"WARNING: {remote_path} still empty after {retries} tries")


def get_pods_json(out_path: Path) -> dict[str, Any]:
    res = kubectl(["get", "pods", "-o", "json"], capture=True)
    out_path.write_text(res.stdout)
    return json.loads(res.stdout)


def to_epoch(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


@dataclass
class McperfSample:
    ts_start: float
    ts_end: float
    qps: float
    p95_ms: float


def parse_mcperf_samples(text: str) -> list[McperfSample]:
    samples: list[McperfSample] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] != "read" or len(parts) < 20:
            continue
        try:
            p95_us = float(parts[12])
            qps = float(parts[16])
            ts_start, ts_end = float(parts[18]), float(parts[19])
            if ts_start > 1e11:
                ts_start, ts_end = ts_start / 1000, ts_end / 1000
            samples.append(McperfSample(ts_start, ts_end, qps, p95_us / 1000))
        except (ValueError, IndexError):
            continue

    if samples:
        return samples

    for block in re.split(r"(?=Timestamp start:)", text):
        if not block.strip():
            continue
        ts_s = re.search(r"Timestamp start:\s*(\d+)", block)
        ts_e = re.search(r"Timestamp end:\s*(\d+)", block)
        qps_m = re.search(r"Total QPS\s*=\s*([\d.]+)", block)
        read_m = re.search(r"^read\s+([\d.]+(?:\s+[\d.]+)+)", block, re.MULTILINE)
        if not (ts_s and ts_e and qps_m and read_m):
            continue
        ts_start, ts_end = float(ts_s.group(1)), float(ts_e.group(1))
        if ts_start > 1e11:
            ts_start, ts_end = ts_start / 1000, ts_end / 1000
        qps = float(qps_m.group(1))
        read_vals = [float(x) for x in read_m.group(1).split()]
        if len(read_vals) < 12:
            continue
        samples.append(McperfSample(ts_start, ts_end, qps, read_vals[11] / 1000))
    return samples


def extract_job_windows(pods_json: dict[str, Any]) -> tuple[dict[str, dict[str, float]], float, float]:
    job_times: dict[str, dict[str, float]] = {}
    starts: list[float] = []
    ends: list[float] = []
    for item in pods_json.get("items", []):
        pod_name = item.get("metadata", {}).get("name", "")
        if pod_name == "some-memcached" or pod_name.startswith("some-memcached"):
            continue
        statuses = item.get("status", {}).get("containerStatuses") or []
        if not statuses:
            continue
        st_obj = statuses[0].get("state", {})
        term = st_obj.get("terminated")
        if not term:
            continue
        st = to_epoch(term["startedAt"])
        en = to_epoch(term["finishedAt"])
        starts.append(st)
        ends.append(en)
        cn = statuses[0].get("name", "")
        job_times[cn] = {"start": st, "end": en, "runtime_s": en - st}

    if not starts or not ends:
        raise RuntimeError("no terminated batch pods in pods json")
    return job_times, min(starts), max(ends)


def longest_violation_streak(samples: list[McperfSample], threshold_ms: float) -> float:
    best = cur = 0.0
    for s in samples:
        dur = max(0.0, s.ts_end - s.ts_start)
        if s.p95_ms > threshold_ms:
            cur += dur
            best = max(best, cur)
        else:
            cur = 0.0
    return best


def evaluate_gates(
    samples: list[McperfSample],
    batch_start: float,
    batch_end: float,
    qps_low: float,
    qps_high: float,
    qps_coverage_min: float,
    violation_max: float,
    max_streak_sec: float,
) -> dict[str, Any]:
    in_window = [s for s in samples if s.ts_start < batch_end and s.ts_end > batch_start]
    if not in_window:
        return {"validity": False, "reason": "no_mcperf_samples_in_batch_window"}

    near = [s for s in in_window if qps_low <= s.qps <= qps_high]
    coverage = len(near) / len(in_window)
    if not near:
        return {"validity": False, "reason": "no_near_30k_samples", "qps_coverage": coverage}

    violations = [s for s in near if s.p95_ms > 1.0]
    violation_ratio = len(violations) / len(near)
    streak = longest_violation_streak(near, 1.0)
    ok = coverage >= qps_coverage_min and violation_ratio <= violation_max and streak <= max_streak_sec
    return {
        "validity": True,
        "qps_coverage": coverage,
        "slo_violation_ratio": violation_ratio,
        "longest_violation_streak_sec": streak,
        "pass_gate": ok,
    }


def load_policies(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["policies"]


def run_policy_once(
    policy: dict[str, Any],
    base_output_dir: Path,
    qps_low: float,
    qps_high: float,
    qps_coverage_min: float,
    violation_max: float,
    max_streak_sec: float,
) -> dict[str, Any]:
    policy_id = policy["id"]
    run_num = 1
    while (base_output_dir / policy_id / f"run{run_num}" / "summary.json").exists():
        run_num += 1
    out = base_output_dir / policy_id / f"run{run_num}"
    out.mkdir(parents=True, exist_ok=True)
    log(f"out -> {out}")
    log(f"=== {policy_id} === {policy.get('description', '')}")

    delete_all_workloads()
    nodes = discover_nodes()
    missing = [n for n in REQUIRED_PART3_NODE_TYPES if n not in nodes]
    if missing:
        raise RuntimeError(f"missing labels {missing}\n{debug_cluster_topology()}")

    mem_node = policy["memcached"]["node"]
    mem_threads = int(policy["memcached"]["threads"])
    write_manifest_and_apply(memcached_manifest(mem_threads, mem_node))
    wait_for_pod_ready("some-memcached")
    svc_ip = get_memcached_nodeport_addr()

    run_remote_mcperf_clients(nodes, svc_ip)

    all_job_names: list[str] = []
    all_job_specs: list[dict[str, Any]] = []
    run_ok = True
    for wave in policy["waves"]:
        wave_job_names = []
        for spec in wave:
            job, node, threads = spec["job"], spec["node"], int(spec["threads"])
            all_job_specs.append(spec)
            write_manifest_and_apply(parsec_job_manifest(job, node, threads))
            jn = f"parsec-{job}"
            wave_job_names.append(jn)
            all_job_names.append(jn)
        if not wait_for_jobs(wave_job_names):
            run_ok = False
            break

    log("stop mcperf")
    for role in ("client-agent-a", "client-agent-b", "client-measure"):
        n = nodes[role]
        vm_ssh(
            n["name"], node_external_ip(n),
            "pkill -f mcperf 2>/dev/null; sleep 1; exit 0",
            check=False, mute_ssh_fail_hints=True, via_stdin=True,
        )
    time.sleep(3)

    pods_path = out / "pods_1.json"
    pods_json = get_pods_json(pods_path)
    mcperf_path = out / "mcperf_1.txt"
    fetch_remote_file(nodes["client-measure"], MCPERF_REMOTE_OUT, mcperf_path)

    completed_jobs: set[str] = set()
    for item in pods_json.get("items", []):
        pod_name = item.get("metadata", {}).get("name", "")
        if pod_name.startswith("some-memcached"):
            continue
        statuses = item.get("status", {}).get("containerStatuses") or []
        if not statuses:
            continue
        state = statuses[0].get("state", {})
        job_name = (statuses[0].get("name") or "").removeprefix("parsec-")
        if state.get("terminated") and job_name in PARSEC_META:
            completed_jobs.add(job_name)

    all_done = len(completed_jobs) == 7
    summary: dict[str, Any] = {
        "policy_id": policy_id,
        "description": policy.get("description", ""),
        "all_jobs_completed": all_done,
        "completed_jobs": sorted(completed_jobs),
        "job_specs": all_job_specs,
    }

    if all_done:
        try:
            job_times, batch_start, batch_end = extract_job_windows(pods_json)
        except RuntimeError as e:
            log(f"WARNING: {e}")
            summary.update({"gates": {"validity": False, "reason": str(e)}, "screening_pass": False})
            (out / "summary.json").write_text(json.dumps(summary, indent=2))
            delete_all_workloads()
            return summary

        makespan = batch_end - batch_start
        samples = parse_mcperf_samples(mcperf_path.read_text())
        gates = evaluate_gates(
            samples,
            batch_start,
            batch_end,
            qps_low,
            qps_high,
            qps_coverage_min,
            violation_max,
            max_streak_sec,
        )
        summary.update({
            "batch_start_epoch": batch_start,
            "batch_end_epoch": batch_end,
            "makespan_s": makespan,
            "job_times_s": job_times,
            "mcperf_samples_total": len(samples),
            "gates": gates,
            "screening_pass": gates.get("validity") and gates.get("pass_gate"),
        })
    else:
        summary.update({
            "gates": {"validity": False, "reason": "not_all_jobs_completed"},
            "screening_pass": False,
        })

    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    delete_all_workloads()
    return summary


def delete_cluster() -> None:
    log("kops delete cluster")
    try:
        run_cmd(["kops", "delete", "cluster", "--name", CLUSTER_NAME, "--yes"])
    except subprocess.CalledProcessError as e:
        log(f"kops delete failed: {e}")


def main() -> None:
    global _KUBECTL_CONTEXT
    parser = argparse.ArgumentParser(description="Part 3a policy screening")
    parser.add_argument("--policy-config", default="part3_policies.json")
    parser.add_argument("--output-dir", default="part3-screening")
    parser.add_argument("--policies", nargs="*", default=[])
    parser.add_argument("--qps-low", type=float, default=29000)
    parser.add_argument("--qps-high", type=float, default=31000)
    parser.add_argument("--qps-coverage-min", type=float, default=0.80)
    parser.add_argument("--violation-max", type=float, default=0.01)
    parser.add_argument("--max-streak-sec", type=float, default=60.0)
    parser.add_argument("--delete-cluster-after", action="store_true")
    parser.add_argument(
        "--kubectl-context",
        default=os.environ.get("KUBECTL_CONTEXT") or "",
    )
    args = parser.parse_args()
    _KUBECTL_CONTEXT = args.kubectl_context.strip() or None

    preflight_kubectl_part3_cluster()

    policy_config = Path(args.policy_config)
    base_out = Path(args.output_dir)
    base_out.mkdir(parents=True, exist_ok=True)

    try:
        policies = load_policies(policy_config)
        if args.policies:
            wanted = set(args.policies)
            policies = [p for p in policies if p["id"] in wanted]
            if not policies:
                print("no matching ids")
                sys.exit(1)

        summaries = [
            run_policy_once(
                p, base_out, args.qps_low, args.qps_high,
                args.qps_coverage_min, args.violation_max, args.max_streak_sec,
            )
            for p in policies
        ]
        for p, s in zip(policies, summaries, strict=True):
            log(
                f"{p['id']} pass={s.get('screening_pass')} "
                f"span={s.get('makespan_s')} viol={s.get('gates', {}).get('slo_violation_ratio')}"
            )

        fp = base_out / "screening_summary.json"
        fp.write_text(json.dumps(summaries, indent=2))
        log(f"wrote {fp}")

        ranked = sorted(
            summaries,
            key=lambda x: (
                0 if x.get("screening_pass") else 1,
                x.get("gates", {}).get("slo_violation_ratio", 1e9),
                x.get("makespan_s", 1e9),
            ),
        )
        for i, r in enumerate(ranked, 1):
            g = r.get("gates") or {}
            log(
                f"{i}. {r.get('policy_id')} pass={r.get('screening_pass')} "
                f"viol={g.get('slo_violation_ratio')} cov={g.get('qps_coverage')} span={r.get('makespan_s')}"
            )

        if args.delete_cluster_after:
            delete_cluster()

    except KeyboardInterrupt:
        log("interrupted — cleanup")
        delete_all_workloads()
        if args.delete_cluster_after:
            delete_cluster()
        sys.exit(1)
    except Exception:
        delete_all_workloads()
        if args.delete_cluster_after:
            delete_cluster()
        raise


if __name__ == "__main__":
    main()
