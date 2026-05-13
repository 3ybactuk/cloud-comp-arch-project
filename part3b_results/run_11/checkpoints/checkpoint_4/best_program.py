from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ZONE = "europe-west1-b"
GCP_PROJECT = os.environ.get("GCP_PROJECT", "cca-eth-2026-group-087")
POLL_INTERVAL_SEC = 10
JOB_TIMEOUT_SEC = 3600

PARSEC_META: dict[str, tuple[str, str]] = {
    "barnes":        ("anakli/cca:splash2x_barnes",      "splash2x"),
    "radix":         ("anakli/cca:splash2x_radix",        "splash2x"),
    "blackscholes":  ("anakli/cca:parsec_blackscholes",   "parsec"),
    "canneal":       ("anakli/cca:parsec_canneal",        "parsec"),
    "freqmine":      ("anakli/cca:parsec_freqmine",       "parsec"),
    "streamcluster": ("anakli/cca:parsec_streamcluster",  "parsec"),
    "vips":          ("anakli/cca:parsec_vips",           "parsec"),
}

ALL_JOBS = list(PARSEC_META.keys())

MCPERF_REMOTE_DIR = "~/memcache-perf-dynamic"
MCPERF_REMOTE_OUT = "/tmp/mcperf_evolve.txt"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def kubectl(args: list[str], capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["kubectl"] + args
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=capture, text=True, check=check)


def run_ssh(node_name: str, command: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [
        "gcloud", "compute", "ssh",
        "--ssh-key-file", os.path.expanduser("~/.ssh/cloud-computing"),
        f"ubuntu@{node_name}",
        "--zone", ZONE,
        "--project", GCP_PROJECT,
        "--", command,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def discover_nodes() -> dict[str, dict[str, str]]:
    result = kubectl(["get", "nodes", "-o", "json"], capture=True)
    data = json.loads(result.stdout)
    by_label: dict[str, dict[str, str]] = {}
    for item in data["items"]:
        labels = item["metadata"].get("labels", {})
        nodetype = labels.get("cca-project-nodetype")
        if not nodetype:
            continue
        addresses = item["status"].get("addresses", [])
        internal_ip = next((a["address"] for a in addresses if a["type"] == "InternalIP"), "")
        external_ip = next((a["address"] for a in addresses if a["type"] == "ExternalIP"), "")
        by_label[nodetype] = {
            "name": item["metadata"]["name"],
            "internal_ip": internal_ip,
            "external_ip": external_ip,
        }
    return by_label


def delete_all_workloads() -> None:
    kubectl(["delete", "jobs", "--all", "--ignore-not-found=true"], check=False)
    kubectl(["delete", "pod", "some-memcached", "--ignore-not-found=true"], check=False)
    kubectl(["delete", "svc", "some-memcached-11211", "--ignore-not-found=true"], check=False)
    time.sleep(5)


def apply_manifest(manifest: str) -> None:
    proc = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest, text=True, capture_output=True, check=True,
    )
    log(proc.stdout.strip())


def memcached_manifest(threads: int, node_label: str) -> str:
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
    args: ["-c", "/memcached/memcached -t {threads} -u memcache"]
    resources:
      requests:
        cpu: "{threads}000m"
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
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        res = kubectl(["get", "pod", name, "-o", "jsonpath={.status.phase}"], capture=True, check=False)
        if res.stdout.strip() == "Running":
            log(f"Pod {name} Running.")
            return
        time.sleep(3)
    raise RuntimeError(f"Timeout waiting for pod {name}")


def get_memcached_addr(timeout_sec: int = 120) -> str:
    """Node InternalIP + NodePort — client VMs are outside pod/ClusterIP network (same as run_part3a)."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        res = kubectl(["get", "pod", "some-memcached", "-o", "jsonpath={.spec.nodeName}"], capture=True, check=False)
        node_name = res.stdout.strip()
        if node_name:
            res2 = kubectl(
                ["get", "node", node_name, "-o", "jsonpath={.status.addresses[?(@.type==\"InternalIP\")].address}"],
                capture=True, check=False,
            )
            ip = res2.stdout.strip()
            if ip:
                addr = f"{ip}:31211"
                log(f"Memcached via NodePort on node {node_name}: {addr}")
                return addr
        time.sleep(5)
    raise RuntimeError("Timeout waiting for memcached node address")


def start_mcperf(nodes: dict[str, dict[str, str]], mem_ip: str) -> None:
    # agent-a
    run_ssh(nodes["client-agent-a"]["name"],
            f"cd {MCPERF_REMOTE_DIR} && nohup ./mcperf -T 2 -A > /tmp/mcperf_agent_a.log 2>&1 &")
    # agent-b
    run_ssh(nodes["client-agent-b"]["name"],
            f"cd {MCPERF_REMOTE_DIR} && nohup ./mcperf -T 4 -A > /tmp/mcperf_agent_b.log 2>&1 &")
    time.sleep(2)
    agent_a_ip = nodes["client-agent-a"]["internal_ip"]
    agent_b_ip = nodes["client-agent-b"]["internal_ip"]
    # load + measure
    run_ssh(nodes["client-measure"]["name"],
            f"cd {MCPERF_REMOTE_DIR} && ./mcperf -s {mem_ip} --loadonly")
    run_ssh(nodes["client-measure"]["name"],
            (f"cd {MCPERF_REMOTE_DIR} && nohup ./mcperf -s {mem_ip}"
             f" -a {agent_a_ip} -a {agent_b_ip}"
             f" --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10"
             f" --scan 30000:30500:5"
             f" > {MCPERF_REMOTE_OUT} 2>&1 &"))
    log("mcperf clients started")


def stop_mcperf(nodes: dict[str, dict[str, str]]) -> None:
    for role in ("client-agent-a", "client-agent-b", "client-measure"):
        run_ssh(nodes[role]["name"], "pkill -f mcperf 2>/dev/null || true", check=False)
    time.sleep(3)


def wait_for_jobs(job_names: list[str], timeout_sec: int = JOB_TIMEOUT_SEC) -> bool:
    pending = set(job_names)
    deadline = time.time() + timeout_sec
    while pending and time.time() < deadline:
        for jn in list(pending):
            res = kubectl(["get", "job", jn, "-o", "jsonpath={.status.conditions}"], capture=True, check=False)
            text = res.stdout
            if '"Complete"' in text:
                log(f"Job {jn} complete.")
                pending.discard(jn)
            elif '"Failed"' in text:
                log(f"Job {jn} FAILED.")
                return False
        if pending:
            time.sleep(POLL_INTERVAL_SEC)
    return len(pending) == 0


def get_pods_json() -> dict:
    res = kubectl(["get", "pods", "-o", "json"], capture=True)
    return json.loads(res.stdout)


def fetch_mcperf_output(nodes: dict[str, dict[str, str]]) -> str:
    res = run_ssh(nodes["client-measure"]["name"], f"cat {MCPERF_REMOTE_OUT}", check=False)
    return res.stdout


def extract_makespan(pods_json: dict) -> tuple[float, float, dict[str, dict[str, float]]]:
    job_times: dict[str, dict[str, float]] = {}
    for item in pods_json.get("items", []):
        labels = item.get("metadata", {}).get("labels", {})
        app = labels.get("app", "")
        if not app.startswith("parsec-"):
            continue
        cs = item.get("status", {}).get("containerStatuses", [])
        if not cs:
            continue
        term = cs[0].get("state", {}).get("terminated")
        if not term:
            continue
        started = term.get("startedAt", "")
        finished = term.get("finishedAt", "")
        if started and finished:
            s = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
            f = datetime.fromisoformat(finished.replace("Z", "+00:00")).timestamp()
            job_times[app] = {"start": s, "end": f, "runtime_s": f - s}
    if not job_times:
        raise RuntimeError("No completed job times found")
    batch_start = min(v["start"] for v in job_times.values())
    batch_end = max(v["end"] for v in job_times.values())
    return batch_start, batch_end, job_times


def parse_mcperf_samples(text: str) -> list[dict[str, float]]:
    samples = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 16:
            continue
        try:
            qps = float(parts[12])
            p95 = float(parts[15]) / 1000.0  # us -> ms
            ts_start = float(parts[-2])
            ts_end = float(parts[-1])
            samples.append({"qps": qps, "p95_ms": p95, "ts_start": ts_start, "ts_end": ts_end})
        except (ValueError, IndexError):
            continue
    return samples


def compute_slo_metrics(samples: list[dict], batch_start: float, batch_end: float,
                         qps_low: float = 29000, qps_high: float = 31000) -> dict:
    in_window = [s for s in samples if s["ts_start"] < batch_end and s["ts_end"] > batch_start]
    near_target = [s for s in in_window if qps_low <= s["qps"] <= qps_high]
    if not near_target:
        return {"violation_ratio": 1.0, "p95_max_ms": 999.0, "coverage": 0.0}
    violations = [s for s in near_target if s["p95_ms"] > 1.0]
    return {
        "violation_ratio": len(violations) / len(near_target),
        "p95_max_ms": max(s["p95_ms"] for s in near_target),
        "coverage": len(near_target) / max(len(in_window), 1),
    }


# EVOLVE-BLOCK-START
def get_policy() -> dict[str, Any]:
    # node-a-8core: 8 CPUs, memcached takes 2, so 6 left for batch
    # node-b-4core: 4 CPUs, all for batch (less RAM though, ~4GB)
    #
    # from part2 measurements:
    #   freqmine ~250s, vips ~200s, canneal ~180s, streamcluster ~150s
    #   blackscholes ~60s, barnes ~50s, radix ~10s
    #
    # freqmine and vips trash the LLC, so running them on node-a next to memcached
    # is risky — either put them on node-b or keep threads low
    #
    # best hand-crafted result so far: 378s (p07)
    # the bottleneck was freqmine taking 167s + canneal 115s in sequential waves
    return {
        "memcached": {
            "node": "node-a-8core",
            "threads": 2,
        },
        "waves": [
            # start the two fast jobs right away on both nodes
            [
                {"job": "blackscholes", "node": "node-a-8core", "threads": 2},
                {"job": "barnes",       "node": "node-b-4core", "threads": 2},
            ],
            # then the medium/long ones — streamcluster scales well so 4 threads is fine
            # freqmine on node-b to avoid LLC pressure on memcached
            [
                {"job": "streamcluster", "node": "node-a-8core", "threads": 4},
                {"job": "freqmine",      "node": "node-b-4core", "threads": 2},
            ],
            # vips + canneal — canneal doesn't scale past 2 threads anyway
            [
                {"job": "vips",    "node": "node-a-8core", "threads": 2},
                {"job": "canneal", "node": "node-b-4core", "threads": 2},
            ],
            # radix last, it's ~10s so doesn't matter much where it lands
            [
                {"job": "radix", "node": "node-a-8core", "threads": 4},
            ],
        ],
    }
# EVOLVE-BLOCK-END


# Main runner

def main() -> None:
    dry_run = "--dry-run" in sys.argv

    policy = get_policy()
    log(f"Policy: {json.dumps(policy, indent=2)}")

    if dry_run:
        print(json.dumps({"dry_run": True, "policy": policy}))
        return

    nodes = discover_nodes()
    delete_all_workloads()

    # Start memcached
    mem_cfg = policy["memcached"]
    apply_manifest(memcached_manifest(mem_cfg["threads"], mem_cfg["node"]))
    wait_for_pod_ready("some-memcached")
    mem_ip = get_memcached_addr()

    # Start mcperf load
    start_mcperf(nodes, mem_ip)

    # Run waves
    batch_start_wall = time.time()
    all_ok = True
    for wave_idx, wave in enumerate(policy["waves"]):
        log(f"Starting wave {wave_idx + 1}: {[s['job'] for s in wave]}")
        wave_job_names = []
        for spec in wave:
            apply_manifest(parsec_job_manifest(spec["job"], spec["node"], spec["threads"]))
            wave_job_names.append(f"parsec-{spec['job']}")
        ok = wait_for_jobs(wave_job_names)
        if not ok:
            log(f"Wave {wave_idx + 1} failed.")
            all_ok = False
            break

    # Collect results
    stop_mcperf(nodes)
    pods_json = get_pods_json()
    mcperf_text = fetch_mcperf_output(nodes)

    result: dict[str, Any] = {"all_jobs_completed": all_ok}

    if all_ok:
        try:
            batch_start, batch_end, job_times = extract_makespan(pods_json)
            makespan = batch_end - batch_start
            samples = parse_mcperf_samples(mcperf_text)
            slo = compute_slo_metrics(samples, batch_start, batch_end)
            result.update({
                "makespan_s": makespan,
                "job_times_s": job_times,
                "violation_ratio": slo["violation_ratio"],
                "p95_max_ms": slo["p95_max_ms"],
                "slo_coverage": slo["coverage"],
            })
            # Score: penalize SLO violations heavily, reward low makespan
            # Baseline makespan = 378s (best hand-crafted); target = 300s
            slo_factor = max(0.0, 1.0 - slo["violation_ratio"] * 20.0)
            makespan_score = min(1.0, 378.0 / max(makespan, 1.0))
            result["score"] = round(slo_factor * makespan_score, 4)
        except Exception as e:
            result["error"] = str(e)
            result["score"] = 0.0
    else:
        result["score"] = 0.0

    log(f"Result: {json.dumps(result, indent=2)}")
    print(json.dumps(result))

    delete_all_workloads()


if __name__ == "__main__":
    main()

