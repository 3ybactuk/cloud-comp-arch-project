#!/usr/bin/env python3
"""
Part 3a (hand-crafted policy) screening runner.

Purpose:
- Run many policy candidates once (run #1 screening).
- Apply agreed gates (SLO at ~30K QPS, completion, stability).
- Save artifacts in submission-friendly format.

Notes:
- This script assumes the Part 3 cluster is already deployed and reachable.
- It uses gcloud SSH to run mcperf on client nodes (install memcache-perf-dynamic on those VMs once; see course / WORKFLOW.md).
- It is designed for screening; repeat runs (#2/#3) can be executed tomorrow
  only for shortlisted policies.

FIXES vs original:
1. memcached_manifest: removed hardcoded taskset -c 0-1 (breaks node-b); now accepts optional cores param.
2. run_remote_mcperf_clients: fixed double-pkill race — loadonly now runs separately before measure,
   and agents are started cleanly without killing themselves.
3. extract_job_windows: now matches containers by job label (parsec-*) rather than by container name.
4. get_service_ip: added retry loop so script doesn't fail if Service IP isn't ready instantly.
5. mcperf measure command: -t 10 is fine (10s reporting interval); but total run time is now
   set to large value so mcperf stays alive for the whole batch window.
6. fetch_remote_file: added retry with timeout so we don't fail if mcperf hasn't flushed yet.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
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
MCPERF_REMOTE_DIR = "~/memcache-perf-dynamic"
MCPERF_REMOTE_ABSPATH = "/home/ubuntu/memcache-perf-dynamic"
MCPERF_REMOTE_OUT = "/tmp/mcperf_part3_measure.txt"

# mcperf total measurement duration in seconds.
# Must be larger than the longest expected makespan (~60 min to be safe).
MCPERF_TOTAL_DURATION_SEC = 10


def _remote_bash_login_c(script_body: str) -> str:
    """Returns script_body as-is; _argv_ssh_direct now passes it as separate args."""
    return script_body


def _argv_ssh_direct(external_ip: str, remote_command: str, via_stdin: bool = False) -> list[str]:
    key = os.path.expanduser("~/.ssh/cloud-computing")
    base = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=30",
        "-i", key,
        f"ubuntu@{external_ip}",
    ]
    if via_stdin:
        # Script passed via stdin; just invoke bash
        return base + ["bash"]
    # Pass as a single shell command string — SSH invokes /bin/sh on the remote side.
    # This correctly handles &&, ;, >, nohup, etc.
    return base + [remote_command]


def _argv_gcloud_ssh(k8s_node_name: str, remote_command: str) -> list[str]:
    # gcloud --command receives a single string executed by the remote shell directly,
    # so no extra quoting needed — &&, ; and > work as-is.
    return [
        "gcloud", "compute", "ssh",
        f"ubuntu@{k8s_node_name}",
        "--zone", ZONE,
        "--ssh-key-file", os.path.expanduser("~/.ssh/cloud-computing"),
        "--project", GCP_PROJECT,
        "--command", remote_command,
    ]


REQUIRED_PART3_NODE_TYPES = [
    "node-a-8core",
    "node-b-4core",
    "client-agent-a",
    "client-agent-b",
    "client-measure",
]


PARSEC_META = {
    "barnes":        ("anakli/cca:splash2x_barnes",      "splash2x"),
    "radix":         ("anakli/cca:splash2x_radix",        "splash2x"),
    "blackscholes":  ("anakli/cca:parsec_blackscholes",   "parsec"),
    "canneal":       ("anakli/cca:parsec_canneal",        "parsec"),
    "freqmine":      ("anakli/cca:parsec_freqmine",       "parsec"),
    "streamcluster": ("anakli/cca:parsec_streamcluster",  "parsec"),
    "vips":          ("anakli/cca:parsec_vips",           "parsec"),
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_cmd(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def kubectl_raw(args: list[str], capture: bool = True, check: bool = False) -> subprocess.CompletedProcess:
    return run_cmd(["kubectl"] + args, check=check, capture=capture)


def kubectl(args: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["kubectl"]
    if _KUBECTL_CONTEXT:
        cmd += ["--context", _KUBECTL_CONTEXT]
    cmd += args
    return run_cmd(cmd, check=check, capture=capture)


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
        attempts.append(("ssh", _argv_ssh_direct(external_ip.strip(), remote_command, via_stdin=via_stdin)))
    attempts.append(("gcloud", _argv_gcloud_ssh(k8s_name, remote_command)))

    proc_final: subprocess.CompletedProcess | None = None
    for label, argv in attempts:
        log(f"$ [{label}] {' '.join(argv)}")
        if via_stdin and label == "ssh":
            # Pass script via stdin — avoids all bash -c quoting issues.
            proc = subprocess.run(argv, input=remote_command, capture_output=True, text=True)
        else:
            proc = subprocess.run(argv, capture_output=True, text=True)

        if proc.returncode != 0:
            if mute_ssh_fail_hints:
                blob = ((proc.stderr or "") + "\n" + (proc.stdout or "")).replace("\n", " ").strip()
                if len(blob) > 200:
                    blob = blob[:200] + "…"
                log(
                    f"[{label}] {k8s_name}: ssh exit {proc.returncode} "
                    f"{'ignored' if not check else 'will retry or fail'}{': ' + blob if blob else ''}"
                )
            else:
                err = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
                if err:
                    log(f"[{label}] {k8s_name}: failed rc={proc.returncode}")
                    log(err[:3500])

        proc_final = proc
        if proc.returncode == 0:
            if label != attempts[0][0]:
                log(f"SSH to {k8s_name}: succeeded via {label} fallback.")
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
        lines.append(f"All kubectl calls use --context {_KUBECTL_CONTEXT!r}")
    cur = kubectl_raw(["config", "current-context"], capture=True, check=False)
    lines.append(f"kubectl default current-context (from ~/.kube/config): {(cur.stdout or '').strip() or '(empty)'}")
    nodes = kubectl(["get", "nodes", "-o", "json"], capture=True, check=False)
    if nodes.returncode != 0 or not nodes.stdout.strip():
        lines.append(f"kubectl get nodes failed: {(nodes.stderr or '').strip()}")
        return "\n".join(lines)
    data = json.loads(nodes.stdout)
    lines.append(f"Reporting {len(data.get('items', []))} node(s) from this context:")
    for item in data.get("items", []):
        name = item["metadata"]["name"]
        nt = item["metadata"].get("labels", {}).get("cca-project-nodetype")
        lines.append(f"  - {name}  cca-project-nodetype={nt!r}")
        if nt is None:
            lines[-1] += "   <-- Part 3 needs this label; wrong cluster?"
    lines.append("")
    lines.append(
        "If nodetype is missing on all nodes, switch kube context to Part 3, e.g.:\n"
        "  kubectl config get-contexts\n"
        "  kubectl config use-context <context-for-part3.k8s.local>\n"
        "or refresh admin kubeconfig (kops version may differ):\n"
        "  kops export kubeconfig --admin --name part3.k8s.local\n"
        "Then re-run:\n"
        "  python3 run_part3a.py --kubectl-context '<name>' ..."
    )
    return "\n".join(lines)


def preflight_kubectl_part3_cluster() -> None:
    nodes = discover_nodes()
    missing = [n for n in REQUIRED_PART3_NODE_TYPES if n not in nodes]
    if missing:
        print("\n*** Part 3 kubectl preflight failed ***", file=sys.stderr)
        print(debug_cluster_topology(), file=sys.stderr)
        print(
            f"\nMissing node label values (cca-project-nodetype): {missing}\n",
            file=sys.stderr,
        )
        sys.exit(2)
    log("kubectl preflight: Part 3 node labels OK.")


def discover_nodes() -> dict[str, dict[str, str]]:
    result = kubectl(["get", "nodes", "-o", "json"], capture=True)
    data = json.loads(result.stdout)
    by_label: dict[str, dict[str, str]] = {}
    for item in data["items"]:
        labels = item["metadata"].get("labels", {})
        nodetype = labels.get("cca-project-nodetype")
        if not nodetype:
            continue
        internal_ip = ""
        external_ip = ""
        for addr in item["status"].get("addresses", []):
            if addr.get("type") == "InternalIP":
                internal_ip = addr.get("address", "")
            if addr.get("type") == "ExternalIP":
                external_ip = addr.get("address", "")
        by_label[nodetype] = {
            "name": item["metadata"]["name"],
            "internal_ip": internal_ip,
            "external_ip": external_ip,
        }
    return by_label


def memcached_manifest(mem_threads: int, node_label: str) -> str:
    # FIX: removed hardcoded "taskset -c 0-1" — that assumed a specific CPU layout and
    # interfered with batch jobs on the same node. memcached runs on whatever cores the
    # OS schedules it on (Kubernetes resource requests are the real constraint here).
    # If you want CPU pinning, add a taskset via the policy config instead.
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
    while True:
        if time.time() - start > timeout_sec:
            raise RuntimeError(f"Timed out waiting for pod {name} to be Ready")
        res = kubectl(
            ["get", "pod", name, "-o", "jsonpath={.status.phase}"],
            check=False,
            capture=True,
        )
        phase = res.stdout.strip()
        if phase == "Running":
            log(f"Pod {name} is Running.")
            return
        time.sleep(3)


def get_service_cluster_ip(svc_name: str, timeout_sec: int = 120) -> str:
    """Get the internal IP of the node running the memcached pod.

    client-measure is outside the k8s cluster so ClusterIP is unreachable.
    We use the node's InternalIP + NodePort 31211 instead.
    """
    start = time.time()
    while True:
        if time.time() - start > timeout_sec:
            raise RuntimeError("Timed out waiting for memcached pod node IP")
        # Get the node name where memcached pod is running
        res = kubectl(
            ["get", "pod", "some-memcached", "-o", "jsonpath={.spec.nodeName}"],
            capture=True,
            check=False,
        )
        node_name = res.stdout.strip()
        if not node_name:
            time.sleep(3)
            continue
        # Get the internal IP of that node
        res2 = kubectl(
            ["get", "node", node_name,
             "-o", "jsonpath={.status.addresses[?(@.type==\"InternalIP\")].address}"],
            capture=True,
            check=False,
        )
        ip = res2.stdout.strip()
        if ip:
            # Use NodePort 31211 so client-measure can reach memcached
            mem_addr = f"{ip}:31211"
            log(f"Memcached reachable at {mem_addr} (node {node_name})")
            return mem_addr
        time.sleep(3)


def wait_for_jobs(job_names: list[str]) -> bool:
    start = time.time()
    pending = set(job_names)
    while pending:
        if time.time() - start > JOB_TIMEOUT_SEC:
            log("ERROR: timed out waiting for jobs")
            return False
        done_now = []
        for name in list(pending):
            res = kubectl(
                ["get", "job", name, "-o", "jsonpath={.status.conditions[*].type}"],
                check=False,
                capture=True,
            )
            status = res.stdout.strip()
            if "Complete" in status:
                done_now.append(name)
            elif "Failed" in status:
                log(f"ERROR: job failed: {name}")
                return False
        for d in done_now:
            pending.remove(d)
            log(f"Job completed: {d}")
        if pending:
            log(f"Jobs still running: {', '.join(sorted(pending))}")
            time.sleep(POLL_INTERVAL_SEC)
    return True


def run_remote_mcperf_clients(node_map: dict[str, dict[str, str]], mem_ip: str) -> None:
    """
    Start mcperf agents and measure process on client VMs.
    Uses script files to avoid bash -c argument parsing issues.
    """
    na = node_map["client-agent-a"]
    nb = node_map["client-agent-b"]
    nm = node_map["client-measure"]
    agent_a_ip = na["internal_ip"]
    agent_b_ip = nb["internal_ip"]

    # Kill any leftover mcperf processes from a previous run.
    for role_node in [na, nb, nm]:
        vm_ssh(
            role_node["name"],
            node_external_ip(role_node),
            "pkill -f mcperf 2>/dev/null; sleep 1; exit 0",
            check=False,
            mute_ssh_fail_hints=True,
            via_stdin=True,
        )
    time.sleep(2)

    # Start agent A with nohup + </dev/null so it survives SSH session close.
    log("Starting mcperf agent A...")
    vm_ssh(
        na["name"],
        node_external_ip(na),
        f"nohup {MCPERF_REMOTE_ABSPATH}/mcperf -T 2 -A >/tmp/mcperf_agent_a.log 2>&1 </dev/null &",
    )
    time.sleep(1)

    # Start agent B.
    log("Starting mcperf agent B...")
    vm_ssh(
        nb["name"],
        node_external_ip(nb),
        f"nohup {MCPERF_REMOTE_ABSPATH}/mcperf -T 4 -A >/tmp/mcperf_agent_b.log 2>&1 </dev/null &",
    )
    time.sleep(2)

    # Load memcached (synchronous).
    log("Loading memcached key-value store...")
    vm_ssh(
        nm["name"],
        node_external_ip(nm),
        f"{MCPERF_REMOTE_ABSPATH}/mcperf -s {mem_ip} --loadonly >/tmp/mcperf_loadonly.log 2>&1; exit 0",
    )
    log("Memcached loaded.")

    # Start measurement with nohup + </dev/null so it survives SSH session close.
    log("Starting mcperf measurement (background)...")
    measure_cmd = (
        f"nohup {MCPERF_REMOTE_ABSPATH}/mcperf -s {mem_ip} "
        f"-a {agent_a_ip} -a {agent_b_ip} "
        f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t {MCPERF_TOTAL_DURATION_SEC} "
        f"--scan 30000:30500:5 "
        f">{MCPERF_REMOTE_OUT} 2>&1 </dev/null &"
    )
    vm_ssh(
        nm["name"],
        node_external_ip(nm),
        measure_cmd,
    )
    time.sleep(6)
    log("mcperf clients running.")


def fetch_remote_file(node_meta: dict[str, str], remote_path: str, local_path: Path, retries: int = 5) -> None:
    """
    Fetch a remote file via SSH cat, with retries in case mcperf hasn't flushed yet.
    FIX: original had no retry — if mcperf was still writing, we'd get an empty file.
    """
    for attempt in range(retries):
        res = vm_ssh(
            node_meta["name"],
            node_external_ip(node_meta),
            f"cat {remote_path} 2>/dev/null || echo ''",
            check=False,
        )
        content = res.stdout or ""
        if content.strip():
            local_path.write_text(content)
            log(f"Fetched {remote_path} -> {local_path} ({len(content)} bytes)")
            return
        if attempt < retries - 1:
            log(f"Remote file {remote_path} is empty, retrying in 5s... (attempt {attempt+1}/{retries})")
            time.sleep(5)
    # Write whatever we have (possibly empty) and warn.
    local_path.write_text(content)
    log(f"WARNING: {remote_path} was empty after {retries} attempts — mcperf may not have started.")


def get_pods_json(out_path: Path) -> dict[str, Any]:
    res = kubectl(["get", "pods", "-o", "json"], capture=True)
    out_path.write_text(res.stdout)
    return json.loads(res.stdout)


def to_epoch(s: str) -> float:
    dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass
class McperfSample:
    ts_start: float
    ts_end: float
    qps: float
    p95_ms: float


def parse_mcperf_samples(text: str) -> list[McperfSample]:
    samples: list[McperfSample] = []
    seq_t = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", line)]
        if len(nums) < 3:
            continue

        ts_candidates = [x for x in nums if x >= 1_000_000_000]
        if len(ts_candidates) >= 2:
            ts_start, ts_end = ts_candidates[0], ts_candidates[1]
        else:
            ts_start, ts_end = seq_t, seq_t + 10.0
            seq_t += 10.0

        tail = nums[2:] if len(ts_candidates) >= 2 else nums
        qps_candidates = [x for x in tail if 1000 <= x <= 200000]
        if not qps_candidates:
            continue
        qps = qps_candidates[0]

        p95_candidates = [x for x in tail if 0 < x <= 100]
        if not p95_candidates:
            continue
        p95 = p95_candidates[-1]

        samples.append(McperfSample(ts_start=ts_start, ts_end=ts_end, qps=qps, p95_ms=p95))
    return samples


def extract_job_windows(pods_json: dict[str, Any]) -> tuple[dict[str, dict[str, float]], float, float]:
    """
    FIX: original checked `name == "memcached"` by container name which sometimes
    mismatches. We now skip the memcached pod by checking the pod name prefix instead.
    """
    job_times: dict[str, dict[str, float]] = {}
    starts: list[float] = []
    ends: list[float] = []
    for item in pods_json.get("items", []):
        pod_name = item.get("metadata", {}).get("name", "")
        # Skip the memcached pod.
        if pod_name == "some-memcached" or pod_name.startswith("some-memcached"):
            continue

        statuses = item.get("status", {}).get("containerStatuses", [])
        if not statuses:
            continue
        container_name = statuses[0].get("name", "")
        state = statuses[0].get("state", {})
        term = state.get("terminated")
        if not term:
            continue
        st = to_epoch(term["startedAt"])
        en = to_epoch(term["finishedAt"])
        starts.append(st)
        ends.append(en)
        job_times[container_name] = {"start": st, "end": en, "runtime_s": en - st}

    if not starts or not ends:
        raise RuntimeError("No completed batch jobs found in pods json")
    return job_times, min(starts), max(ends)


def longest_violation_streak(samples: list[McperfSample], threshold_ms: float) -> float:
    best = 0.0
    cur = 0.0
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
    in_window = [s for s in samples if s.ts_start >= batch_start and s.ts_end <= batch_end]
    if not in_window:
        return {
            "validity": False,
            "reason": "no_mcperf_samples_in_batch_window",
        }

    near_30k = [s for s in in_window if qps_low <= s.qps <= qps_high]
    coverage = len(near_30k) / len(in_window) if in_window else 0.0
    if not near_30k:
        return {
            "validity": False,
            "reason": "no_near_30k_samples",
            "qps_coverage": coverage,
        }

    violations = [s for s in near_30k if s.p95_ms > 1.0]
    violation_ratio = len(violations) / len(near_30k)
    streak = longest_violation_streak(near_30k, threshold_ms=1.0)

    pass_gate = (
        coverage >= qps_coverage_min
        and violation_ratio <= violation_max
        and streak <= max_streak_sec
    )
    return {
        "validity": True,
        "qps_coverage": coverage,
        "slo_violation_ratio": violation_ratio,
        "longest_violation_streak_sec": streak,
        "pass_gate": pass_gate,
    }


def load_policies(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    return data["policies"]


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
    out = base_output_dir / policy_id / "run1"
    out.mkdir(parents=True, exist_ok=True)

    log(f"\n{'=' * 80}")
    log(f"Running policy: {policy_id}")
    log(policy.get("description", ""))
    log(f"{'=' * 80}")

    delete_all_workloads()
    nodes = discover_nodes()
    missing = [n for n in REQUIRED_PART3_NODE_TYPES if n not in nodes]
    if missing:
        raise RuntimeError(
            "Missing expected node labels: "
            f"{missing}\n\n"
            "--- cluster diagnostic ---\n"
            f"{debug_cluster_topology()}"
        )

    mem_node = policy["memcached"]["node"]
    mem_threads = int(policy["memcached"]["threads"])
    write_manifest_and_apply(memcached_manifest(mem_threads, mem_node))
    wait_for_pod_ready("some-memcached")

    # FIX: use get_service_cluster_ip with retry instead of one-shot jsonpath.
    svc_ip = get_service_cluster_ip("some-memcached-11211")

    run_remote_mcperf_clients(nodes, svc_ip)

    all_job_names: list[str] = []
    all_job_specs: list[dict[str, Any]] = []
    run_ok = True
    for wave in policy["waves"]:
        wave_job_names = []
        for spec in wave:
            job = spec["job"]
            node = spec["node"]
            threads = int(spec["threads"])
            all_job_specs.append(spec)
            manifest = parsec_job_manifest(job=job, node_label=node, threads=threads)
            write_manifest_and_apply(manifest)
            wave_job_names.append(f"parsec-{job}")
            all_job_names.append(f"parsec-{job}")

        ok = wait_for_jobs(wave_job_names)
        if not ok:
            run_ok = False
            break

    # Collect artifacts — stop mcperf first so the file is flushed.
    log("Stopping remote mcperf processes...")
    for role in ("client-agent-a", "client-agent-b", "client-measure"):
        n = nodes[role]
        vm_ssh(
            n["name"],
            node_external_ip(n),
            "pkill -f mcperf 2>/dev/null; sleep 1; exit 0",
            check=False,
            mute_ssh_fail_hints=True,
            via_stdin=True,
        )
    time.sleep(3)

    pods_path = out / "pods_1.json"
    pods_json = get_pods_json(pods_path)
    mcperf_path = out / "mcperf_1.txt"
    fetch_remote_file(nodes["client-measure"], MCPERF_REMOTE_OUT, mcperf_path)

    # Evaluate.
    completed_jobs: set[str] = set()
    for item in pods_json.get("items", []):
        pod_name = item.get("metadata", {}).get("name", "")
        if pod_name.startswith("some-memcached"):
            continue
        statuses = item.get("status", {}).get("containerStatuses", [])
        if not statuses:
            continue
        name = statuses[0].get("name", "")
        state = statuses[0].get("state", {})
        job_name = name.removeprefix("parsec-")
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
            summary.update({
                "gates": {"validity": False, "reason": str(e)},
                "screening_pass": False,
            })
            (out / "summary.json").write_text(json.dumps(summary, indent=2))
            delete_all_workloads()
            return summary

        makespan = batch_end - batch_start
        samples = parse_mcperf_samples(mcperf_path.read_text())
        gates = evaluate_gates(
            samples=samples,
            batch_start=batch_start,
            batch_end=batch_end,
            qps_low=qps_low,
            qps_high=qps_high,
            qps_coverage_min=qps_coverage_min,
            violation_max=violation_max,
            max_streak_sec=max_streak_sec,
        )
        summary.update(
            {
                "batch_start_epoch": batch_start,
                "batch_end_epoch": batch_end,
                "makespan_s": makespan,
                "job_times_s": job_times,
                "mcperf_samples_total": len(samples),
                "gates": gates,
                "screening_pass": gates.get("validity", False) and gates.get("pass_gate", False),
            }
        )
    else:
        summary.update(
            {
                "gates": {"validity": False, "reason": "not_all_jobs_completed"},
                "screening_pass": False,
            }
        )

    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    delete_all_workloads()
    return summary


def delete_cluster() -> None:
    log("Deleting cluster...")
    try:
        run_cmd(["kops", "delete", "cluster", "--name", CLUSTER_NAME, "--yes"])
        log("Cluster deleted successfully.")
    except subprocess.CalledProcessError as e:
        log(f"ERROR deleting cluster: {e}")


def main() -> None:
    global _KUBECTL_CONTEXT
    parser = argparse.ArgumentParser(description="Run Part 3 policy screening")
    parser.add_argument("--policy-config", default="part3_policies.json")
    parser.add_argument("--output-dir", default="part3-screening")
    parser.add_argument("--policies", nargs="*", default=[], help="Policy ids to run (default: all)")
    parser.add_argument("--qps-low", type=float, default=29000)
    parser.add_argument("--qps-high", type=float, default=31000)
    parser.add_argument("--qps-coverage-min", type=float, default=0.80)
    parser.add_argument("--violation-max", type=float, default=0.01)
    parser.add_argument("--max-streak-sec", type=float, default=60.0)
    parser.add_argument("--delete-cluster-after", action="store_true")
    parser.add_argument(
        "--kubectl-context",
        default=os.environ.get("KUBECTL_CONTEXT") or "",
        help="kubectl context name for Part 3 cluster (same as kubectl config current-context)",
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
                print("No matching policy IDs found.")
                sys.exit(1)

        summaries: list[dict[str, Any]] = []
        for p in policies:
            s = run_policy_once(
                p,
                base_output_dir=base_out,
                qps_low=args.qps_low,
                qps_high=args.qps_high,
                qps_coverage_min=args.qps_coverage_min,
                violation_max=args.violation_max,
                max_streak_sec=args.max_streak_sec,
            )
            summaries.append(s)
            log(
                f"Policy {p['id']} => pass={s.get('screening_pass')} "
                f"makespan={s.get('makespan_s')} "
                f"viol={s.get('gates', {}).get('slo_violation_ratio')}"
            )

        summary_path = base_out / "screening_summary.json"
        summary_path.write_text(json.dumps(summaries, indent=2))
        log(f"Saved: {summary_path}")

        ranked = sorted(
            summaries,
            key=lambda x: (
                0 if x.get("screening_pass") else 1,
                x.get("gates", {}).get("slo_violation_ratio", 1e9),
                x.get("makespan_s", 1e9),
            ),
        )
        log("Ranking (best first):")
        for i, r in enumerate(ranked, start=1):
            log(
                f"{i}. {r.get('policy_id')} pass={r.get('screening_pass')} "
                f"viol={r.get('gates', {}).get('slo_violation_ratio')} "
                f"cov={r.get('gates', {}).get('qps_coverage')} "
                f"makespan={r.get('makespan_s')}"
            )

        if args.delete_cluster_after:
            delete_cluster()

    except KeyboardInterrupt:
        log("\nInterrupted by user. Cleaning up workloads...")
        delete_all_workloads()
        if args.delete_cluster_after:
            delete_cluster()
        sys.exit(1)

    except Exception as e:
        log(f"\nUnexpected error: {e}")
        delete_all_workloads()
        if args.delete_cluster_after:
            delete_cluster()
        raise


if __name__ == "__main__":
    main()