#!/usr/bin/env python3
"""
Part 2b experiment runner
Runs all PARSEC benchmarks with 1, 2, 4, 8 threads (no interference)
Saves results to part2b-experiments/run{N}/

Usage:
    python run_part2b.py --run 1
    python run_part2b.py --runs 2 3 --delete-cluster-after
    python run_part2b.py --runs 1 2 3 --delete-cluster-after
    python run_part2b.py --run 1 --resume
"""

import subprocess
import argparse
import time
import csv
import re
import sys
import os
import tempfile
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

THREAD_COUNTS = [1, 2, 4, 8]

PARSEC_YAML_DIR = "parsec-benchmarks/part2b"
CLUSTER_NAME = "part2b.k8s.local"

POLL_INTERVAL_SEC = 15
JOB_TIMEOUT_SEC = 60 * 60  # 1 hour (native dataset can be slow with 1 thread)

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_cmd(cmd: list[str], check=True, capture=False) -> subprocess.CompletedProcess:
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def kubectl(args: list[str], capture=False, check=True) -> subprocess.CompletedProcess:
    return run_cmd(["kubectl"] + args, capture=capture, check=check)


def delete_cluster():
    log("Deleting cluster...")
    try:
        run_cmd(["kops", "delete", "cluster", CLUSTER_NAME, "--yes"])
        log("Cluster deleted successfully.")
    except subprocess.CalledProcessError as e:
        log(f"ERROR deleting cluster: {e}")


def label_parsec_node():
    log("Labelling parsec node...")
    result = kubectl(["get", "nodes", "-o", "wide"], capture=True)
    for line in result.stdout.splitlines():
        if "parsec" in line.lower():
            node_name = line.split()[0]
            log(f"Found parsec node: {node_name}")
            kubectl(["label", "nodes", node_name, "cca-project-nodetype=parsec", "--overwrite"])
            return
    log("WARNING: Could not find parsec node to label!")


def cleanup_all():
    log("Cleaning up jobs and pods...")
    kubectl(["delete", "jobs", "--all"], check=False)
    kubectl(["delete", "pods", "--all"], check=False)
    time.sleep(10)


def get_job_name(benchmark: str) -> str:
    return f"parsec-{benchmark}"


def create_patched_yaml(benchmark: str, num_threads: int) -> str:
    """
    Read the original YAML, patch the -n <threads> argument, write to a temp file.
    Returns the path to the temp file.
    """
    orig_yaml = Path(PARSEC_YAML_DIR) / f"parsec-{benchmark}.yaml"
    if not orig_yaml.exists():
        raise FileNotFoundError(f"YAML not found: {orig_yaml}")

    content = orig_yaml.read_text()

    # Replace -n <number> in the args string, e.g. "-n 1" -> "-n 4"
    patched = re.sub(r'(-n\s+)\d+', lambda m: m.group(1) + str(num_threads), content)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False,
        prefix=f"parsec-{benchmark}-t{num_threads}-"
    )
    tmp.write(patched)
    tmp.close()
    return tmp.name


def start_benchmark(benchmark: str, num_threads: int):
    log(f"Starting benchmark: {benchmark} with {num_threads} thread(s)")
    tmp_yaml = create_patched_yaml(benchmark, num_threads)
    try:
        kubectl(["create", "-f", tmp_yaml])
    finally:
        os.unlink(tmp_yaml)


def wait_for_job(job_name: str) -> bool:
    start = time.time()
    log(f"Waiting for job '{job_name}' to complete...")
    while True:
        elapsed = time.time() - start
        if elapsed > JOB_TIMEOUT_SEC:
            log(f"ERROR: Job '{job_name}' timed out after {JOB_TIMEOUT_SEC}s!")
            return False
        result = kubectl(
            ["get", "jobs", job_name, "-o", "jsonpath={.status.conditions[*].type}"],
            capture=True, check=False,
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
    pods_result = kubectl(
        ["get", "pods", f"--selector=job-name={job_name}",
         "--output=jsonpath={.items[*].metadata.name}"],
        capture=True, check=False,
    )
    pod_name = pods_result.stdout.strip()
    if not pod_name:
        log(f"WARNING: No pod found for job {job_name}")
        return ""
    logs_result = kubectl(["logs", pod_name], capture=True, check=False)
    return logs_result.stdout


def parse_execution_time(logs: str) -> float | None:
    # "Execution time: 123.4s"
    m = re.search(r"[Ee]xecution\s+time[:\s]+([\d.]+)s?", logs)
    if m:
        return float(m.group(1))
    # "real 2m3.456s"
    m = re.search(r"real\s+(\d+)m([\d.]+)s", logs)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    # "Elapsed time: 123.4"
    m = re.search(r"[Ee]lapsed\s+time[:\s]+([\d.]+)", logs)
    if m:
        return float(m.group(1))
    # bare "real 123.4"
    m = re.search(r"real\s+([\d.]+)", logs)
    if m:
        return float(m.group(1))
    return None


# ── Main experiment loop ──────────────────────────────────────────────────────

def result_path(output_dir: Path, benchmark: str, num_threads: int) -> Path:
    return output_dir / f"{benchmark}_t{num_threads}.txt"


def already_done(output_dir: Path, benchmark: str, num_threads: int) -> bool:
    p = result_path(output_dir, benchmark, num_threads)
    return p.exists() and p.stat().st_size > 0


def run_single_experiment(benchmark: str, num_threads: int, output_dir: Path) -> float | None:
    log(f"\n{'='*60}")
    log(f"  Benchmark:    {benchmark}")
    log(f"  Threads:      {num_threads}")
    log(f"{'='*60}")

    out_file = result_path(output_dir, benchmark, num_threads)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    cleanup_all()

    try:
        start_benchmark(benchmark, num_threads)
        job_name = get_job_name(benchmark)
        success = wait_for_job(job_name)
        logs = get_job_logs(job_name)
        exec_time = parse_execution_time(logs) if success else None

        with open(out_file, "w") as f:
            f.write(f"benchmark: {benchmark}\n")
            f.write(f"threads: {num_threads}\n")
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
        cleanup_all()


def write_csv(results: list[dict], csv_path: Path):
    if not results:
        return
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["benchmark", "threads", "execution_time_s", "success"])
        writer.writeheader()
        writer.writerows(results)
    log(f"Results saved to {csv_path}")


def run_single_run(run_number: int, resume: bool):
    """Run all experiments (7 benchmarks x 4 thread counts = 28) for one run number."""
    output_dir = Path(f"part2b-experiments/run{run_number}")
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "results.csv"

    log(f"\n{'#'*60}")
    log(f"  Starting Run {run_number}")
    log(f"{'#'*60}")
    log(f"Output directory: {output_dir}")
    log(f"Total experiments: {len(BENCHMARKS)} x {len(THREAD_COUNTS)} = "
        f"{len(BENCHMARKS) * len(THREAD_COUNTS)}")

    label_parsec_node()

    results = []
    total = len(BENCHMARKS) * len(THREAD_COUNTS)
    done = 0

    for benchmark in BENCHMARKS:
        for num_threads in THREAD_COUNTS:
            done += 1
            log(f"\nProgress: {done}/{total} (Run {run_number})")

            if resume and already_done(output_dir, benchmark, num_threads):
                log(f"SKIP (already done): {benchmark} t={num_threads}")
                p = result_path(output_dir, benchmark, num_threads)
                with open(p) as f:
                    content = f.read()
                m = re.search(r"execution_time_s: ([\d.]+|None)", content)
                exec_time = None
                if m and m.group(1) != "None":
                    exec_time = float(m.group(1))
                results.append({
                    "benchmark": benchmark,
                    "threads": num_threads,
                    "execution_time_s": exec_time,
                    "success": exec_time is not None,
                })
                continue

            exec_time = run_single_experiment(benchmark, num_threads, output_dir)
            results.append({
                "benchmark": benchmark,
                "threads": num_threads,
                "execution_time_s": exec_time,
                "success": exec_time is not None,
            })
            write_csv(results, csv_path)

    log(f"\n{'='*60}")
    log(f"Run {run_number} done! Results in {output_dir}")
    log(f"{'='*60}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run Part 2b PARSEC parallel experiments")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", type=int, help="Single run number (e.g. --run 1)")
    group.add_argument("--runs", type=int, nargs="+", help="Multiple run numbers (e.g. --runs 1 2 3)")

    parser.add_argument("--resume", action="store_true",
                        help="Skip already completed experiments")
    parser.add_argument("--delete-cluster-after", action="store_true",
                        help="Delete the kops cluster after all runs finish")
    args = parser.parse_args()

    run_numbers = [args.run] if args.run else args.runs

    if any(r < 1 for r in run_numbers):
        print("ERROR: run numbers must be >= 1")
        sys.exit(1)

    try:
        for i, run_number in enumerate(run_numbers):
            is_last = (i == len(run_numbers) - 1)
            run_single_run(run_number, args.resume)

            if args.delete_cluster_after and is_last:
                delete_cluster()
            elif not is_last:
                log(f"\nRun {run_number} done, starting run {run_numbers[i+1]}...")

        log("\nAll runs completed!")

    except KeyboardInterrupt:
        log("\nInterrupted by user. Cleaning up...")
        cleanup_all()
        if args.delete_cluster_after:
            delete_cluster()
        sys.exit(1)

    except Exception as e:
        log(f"\nUnexpected error: {e}")
        cleanup_all()
        if args.delete_cluster_after:
            delete_cluster()
        raise


if __name__ == "__main__":
    main()

