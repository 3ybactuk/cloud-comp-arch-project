#!/usr/bin/env python3
"""
Part 2a experiment runner
Runs all PARSEC benchmarks with all interference types (+ baseline)
Saves results to part2a-experiments/run{N}/

Usage:
    python run_part2a.py --run 1
    python run_part2a.py --run 1 --delete-cluster-after
    python run_part2a.py --run 1 --resume          # skip already done experiments
"""

import subprocess
import argparse
import time
import os
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

BENCHMARKS = [
    "barnes",
    "blackscholes",
    "canneal",
    "freqmine",
    "radix",
    "streamcluster",
    "vips",
]

INTERFERENCE_TYPES = [
    "baseline",   # no interference
    "ibench-cpu",
    "ibench-l1d",
    "ibench-l1i",
    "ibench-l2",
    "ibench-llc",
    "ibench-membw",
]

PARSEC_YAML_DIR = "parsec-benchmarks/part2a"
INTERFERENCE_YAML_DIR = "interference/part2a"
CLUSTER_NAME = "part2a.k8s.local"

# How long to wait for interference to start before running benchmark (seconds)
INTERFERENCE_WARMUP_SEC = 30

# How long to wait between checking if a job is done (seconds)
POLL_INTERVAL_SEC = 15

# Max time to wait for a job to complete (seconds) — 30 minutes
JOB_TIMEOUT_SEC = 30 * 60

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_cmd(cmd: list[str], check=True, capture=False) -> subprocess.CompletedProcess:
    """Run a shell command, optionally capturing output."""
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def kubectl(args: list[str], capture=False, check=True) -> subprocess.CompletedProcess:
    return run_cmd(["kubectl"] + args, capture=capture, check=check)


def label_parsec_node():
    """Label the parsec node so jobs can be scheduled on it."""
    log("Labelling parsec node...")
    result = kubectl(["get", "nodes", "-o", "wide"], capture=True)
    for line in result.stdout.splitlines():
        if "parsec" in line.lower():
            node_name = line.split()[0]
            log(f"Found parsec node: {node_name}")
            kubectl([
                "label", "nodes", node_name,
                "cca-project-nodetype=parsec",
                "--overwrite"
            ])
            return
    log("WARNING: Could not find parsec node to label!")


def cleanup_all():
    """Delete all jobs and pods."""
    log("Cleaning up jobs and pods...")
    kubectl(["delete", "jobs", "--all"], check=False)
    kubectl(["delete", "pods", "--all"], check=False)
    # Wait a bit for cleanup
    time.sleep(10)


def start_interference(interference_type: str):
    """Start an interference workload. Returns immediately for baseline."""
    if interference_type == "baseline":
        log("No interference (baseline)")
        return

    yaml_file = f"{INTERFERENCE_YAML_DIR}/{interference_type}.yaml"
    log(f"Starting interference: {interference_type}")
    kubectl(["create", "-f", yaml_file])

    log(f"Waiting {INTERFERENCE_WARMUP_SEC}s for interference to stabilise...")
    time.sleep(INTERFERENCE_WARMUP_SEC)

    # Verify interference pod is running
    result = kubectl(["get", "pods", "-o", "wide"], capture=True)
    log(f"Pods:\n{result.stdout}")


def get_job_name(benchmark: str) -> str:
    return f"parsec-{benchmark}"


def start_benchmark(benchmark: str):
    yaml_file = f"{PARSEC_YAML_DIR}/parsec-{benchmark}.yaml"
    log(f"Starting benchmark: {benchmark}")
    kubectl(["create", "-f", yaml_file])


def wait_for_job(job_name: str) -> bool:
    """
    Poll until the job completes (Succeeded) or times out.
    Returns True if succeeded, False if timed out or failed.
    """
    start = time.time()
    log(f"Waiting for job '{job_name}' to complete...")

    while True:
        elapsed = time.time() - start
        if elapsed > JOB_TIMEOUT_SEC:
            log(f"ERROR: Job '{job_name}' timed out after {JOB_TIMEOUT_SEC}s!")
            return False

        result = kubectl(
            ["get", "jobs", job_name, "-o", "jsonpath={.status.conditions[*].type}"],
            capture=True,
            check=False,
        )
        status = result.stdout.strip()

        if "Complete" in status:
            log(f"Job '{job_name}' completed successfully ({elapsed:.0f}s)")
            return True
        if "Failed" in status:
            log(f"ERROR: Job '{job_name}' failed!")
            return False

        log(f"  Still running... ({elapsed:.0f}s elapsed)")
        time.sleep(POLL_INTERVAL_SEC)


def get_job_logs(job_name: str) -> str:
    """Get logs from the pod belonging to a job."""
    result = kubectl(
        [
            "logs",
            f"$(kubectl get pods --selector=job-name={job_name} "
            f"--output=jsonpath='{{.items[*].metadata.name}}')",
        ],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        # kubectl doesn't expand $() — use two steps instead
        pods_result = kubectl(
            [
                "get", "pods",
                f"--selector=job-name={job_name}",
                "--output=jsonpath={.items[*].metadata.name}",
            ],
            capture=True,
            check=False,
        )
        pod_name = pods_result.stdout.strip()
        if not pod_name:
            log(f"WARNING: No pod found for job {job_name}")
            return ""
        logs_result = kubectl(["logs", pod_name], capture=True, check=False)
        return logs_result.stdout
    return result.stdout


def parse_execution_time(logs: str) -> float | None:
    """
    Parse PARSEC execution time from job logs.
    PARSEC prints something like:
        real	2m3.456s
    or:
        Elapsed time: 123.456
    Returns time in seconds, or None if not found.
    """
    # Try "real Xm Y.Zs" format (bash time output)
    m = re.search(r"real\s+(\d+)m([\d.]+)s", logs)
    if m:
        minutes = int(m.group(1))
        seconds = float(m.group(2))
        return minutes * 60 + seconds

    # Try "Elapsed time: X.Y" format
    m = re.search(r"[Ee]lapsed\s+time[:\s]+([\d.]+)", logs)
    if m:
        return float(m.group(1))

    # Try "[PARSEC] [---------- Beginning of output ----------]" ... timing
    m = re.search(r"real\s+([\d.]+)", logs)
    if m:
        return float(m.group(1))

    return None


# ── Main experiment loop ──────────────────────────────────────────────────────

def result_path(output_dir: Path, interference: str, benchmark: str) -> Path:
    return output_dir / interference / f"{benchmark}.txt"


def already_done(output_dir: Path, interference: str, benchmark: str) -> bool:
    p = result_path(output_dir, interference, benchmark)
    return p.exists() and p.stat().st_size > 0


def run_single_experiment(
    benchmark: str,
    interference: str,
    output_dir: Path,
) -> float | None:
    """
    Run one (benchmark, interference) experiment.
    Returns execution time in seconds, or None on failure.
    """
    log(f"\n{'='*60}")
    log(f"  Benchmark:    {benchmark}")
    log(f"  Interference: {interference}")
    log(f"{'='*60}")

    out_file = result_path(output_dir, interference, benchmark)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Always clean up before starting to ensure no leftover jobs/pods
    cleanup_all()

    try:
        # 1. Start interference
        start_interference(interference)

        # 2. Start benchmark
        start_benchmark(benchmark)
        job_name = get_job_name(benchmark)

        # 3. Wait for completion
        success = wait_for_job(job_name)

        # 4. Collect logs
        logs = get_job_logs(job_name)
        exec_time = parse_execution_time(logs) if success else None

        # 5. Save logs to file
        with open(out_file, "w") as f:
            f.write(f"benchmark: {benchmark}\n")
            f.write(f"interference: {interference}\n")
            f.write(f"success: {success}\n")
            f.write(f"execution_time_s: {exec_time}\n")
            f.write(f"timestamp: {datetime.now().isoformat()}\n")
            f.write("\n--- raw logs ---\n")
            f.write(logs)

        if exec_time:
            log(f"Execution time: {exec_time:.1f}s")
        else:
            log("WARNING: Could not parse execution time from logs")

        return exec_time

    finally:
        # Always clean up, even if something went wrong
        cleanup_all()


def write_csv(results: list[dict], csv_path: Path):
    """Write results to a CSV file."""
    if not results:
        return
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["benchmark", "interference", "execution_time_s", "success"])
        writer.writeheader()
        writer.writerows(results)
    log(f"Results saved to {csv_path}")


def run_all(run_number: int, resume: bool, delete_cluster: bool):
    output_dir = Path(f"part2a-experiments/run{run_number}")
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "results.csv"

    log(f"Starting Part 2a experiments — Run {run_number}")
    log(f"Output directory: {output_dir}")
    log(f"Total experiments: {len(BENCHMARKS)} × {len(INTERFERENCE_TYPES)} = "
        f"{len(BENCHMARKS) * len(INTERFERENCE_TYPES)}")

    # Label the parsec node first
    label_parsec_node()

    results = []
    total = len(BENCHMARKS) * len(INTERFERENCE_TYPES)
    done = 0

    for interference in INTERFERENCE_TYPES:
        for benchmark in BENCHMARKS:
            done += 1
            log(f"\nProgress: {done}/{total}")

            if resume and already_done(output_dir, interference, benchmark):
                log(f"SKIP (already done): {benchmark} + {interference}")
                # Still load result for CSV
                p = result_path(output_dir, interference, benchmark)
                with open(p) as f:
                    content = f.read()
                m = re.search(r"execution_time_s: ([\d.]+|None)", content)
                exec_time = None
                if m and m.group(1) != "None":
                    exec_time = float(m.group(1))
                results.append({
                    "benchmark": benchmark,
                    "interference": interference,
                    "execution_time_s": exec_time,
                    "success": exec_time is not None,
                })
                continue

            exec_time = run_single_experiment(benchmark, interference, output_dir)
            results.append({
                "benchmark": benchmark,
                "interference": interference,
                "execution_time_s": exec_time,
                "success": exec_time is not None,
            })

            # Save CSV after every experiment (so progress isn't lost)
            write_csv(results, csv_path)

    log(f"\n{'='*60}")
    log(f"All experiments done! Results in {output_dir}")
    log(f"{'='*60}")

    if delete_cluster:
        log("Deleting cluster...")
        try:
            run_cmd(["kops", "delete", "cluster", CLUSTER_NAME, "--yes"])
            log("Cluster deleted successfully.")
        except subprocess.CalledProcessError as e:
            log(f"ERROR deleting cluster: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run Part 2a PARSEC experiments")
    parser.add_argument("--run", type=int, required=True, help="Run number (1, 2, 3)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip already completed experiments")
    parser.add_argument("--delete-cluster-after", action="store_true",
                        help="Delete the kops cluster after all experiments finish")
    args = parser.parse_args()

    if args.run < 1:
        print("ERROR: --run must be >= 1")
        sys.exit(1)

    try:
        run_all(args.run, args.resume, args.delete_cluster_after)
    except KeyboardInterrupt:
        log("\nInterrupted by user. Cleaning up...")
        cleanup_all()
        sys.exit(1)


if __name__ == "__main__":
    main()