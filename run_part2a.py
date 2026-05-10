#!/usr/bin/env python3
# python run_part2a.py --run 1
# python run_part2a.py --runs 1 2 3 --delete-cluster-after

import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


BENCHMARKS = [
    "barnes",
    "blackscholes",
    "canneal",
    "freqmine",
    "radix",
    "streamcluster",
    "vips",
]

INTERFERENCE = [
    "baseline",
    "ibench-cpu",
    "ibench-l1d",
    "ibench-l1i",
    "ibench-l2",
    "ibench-llc",
    "ibench-membw",
]

PARSEC_DIR = "parsec-benchmarks/part2a"
INTERFERENCE_DIR = "interference/part2a"

CLUSTER_NAME = "part2a.k8s.local"

POLL_TIME = 15
TIMEOUT = 30 * 60
WARMUP = 30


def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def run(cmd, check=True, capture=False):
    log("$ " + " ".join(cmd))

    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def kubectl(args, **kwargs):
    return run(["kubectl"] + args, **kwargs)


def cleanup():
    log("Cleaning cluster...")

    kubectl(["delete", "jobs", "--all"], check=False)
    kubectl(["delete", "pods", "--all"], check=False)

    time.sleep(8)


def label_node():
    log("Looking for parsec node...")

    result = kubectl(["get", "nodes", "-o", "wide"], capture=True)

    for line in result.stdout.splitlines():
        if "parsec" not in line.lower():
            continue

        node = line.split()[0]

        log(f"Found node: {node}")

        kubectl([
            "label",
            "nodes",
            node,
            "cca-project-nodetype=parsec",
            "--overwrite",
        ])

        return

    log("Couldn't find parsec node")


def start_interference(interference):
    if interference == "baseline":
        log("Running baseline")
        return

    yaml_path = f"{INTERFERENCE_DIR}/{interference}.yaml"

    log(f"Starting interference: {interference}")
    kubectl(["create", "-f", yaml_path])

    log(f"Waiting {WARMUP}s...")
    time.sleep(WARMUP)


def start_benchmark(benchmark):
    yaml_path = f"{PARSEC_DIR}/parsec-{benchmark}.yaml"
    log(f"Starting benchmark: {benchmark}")
    kubectl(["create", "-f", yaml_path])


def wait_for_job(job_name):
    start = time.time()

    while True:
        elapsed = int(time.time() - start)
        if elapsed > TIMEOUT:
            log(f"{job_name} timed out")
            return False

        result = kubectl(
            [
                "get",
                "jobs",
                job_name,
                "-o",
                "jsonpath={.status.conditions[*].type}",
            ],
            capture=True,
            check=False,
        )

        status = result.stdout.strip()

        if "Complete" in status:
            log(f"{job_name} finished in {elapsed}s")
            return True

        if "Failed" in status:
            log(f"{job_name} failed")
            return False

        log(f"{job_name} still running... ({elapsed}s)")
        time.sleep(POLL_TIME)


def get_logs(job_name):
    result = kubectl(
        [
            "get",
            "pods",
            f"--selector=job-name={job_name}",
            "-o",
            "jsonpath={.items[*].metadata.name}",
        ],
        capture=True,
        check=False,
    )

    pod = result.stdout.strip()

    if not pod:
        return ""

    logs = kubectl(["logs", pod], capture=True, check=False)

    return logs.stdout


def parse_time(logs):
    patterns = [
        r"real\s+(\d+)m([\d.]+)s",
        r"[Ee]lapsed\s+time[:\s]+([\d.]+)",
        r"real\s+([\d.]+)",
    ]

    m = re.search(patterns[0], logs)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))

    for p in patterns[1:]:
        m = re.search(p, logs)

        if m:
            return float(m.group(1))

    return None


def save_result(path, benchmark, interference, success, exec_time, logs):
    with open(path, "w") as f:
        f.write(f"benchmark: {benchmark}\n")
        f.write(f"interference: {interference}\n")
        f.write(f"success: {success}\n")
        f.write(f"execution_time_s: {exec_time}\n")
        f.write(f"time: {datetime.now().isoformat()}\n")

        f.write("\n--- logs ---\n")
        f.write(logs)


def run_experiment(benchmark, interference, out_dir):
    log("=" * 50)
    log(f"{benchmark} + {interference}")

    cleanup()

    try:
        start_interference(interference)
        start_benchmark(benchmark)
        job_name = f"parsec-{benchmark}"
        success = wait_for_job(job_name)
        logs = get_logs(job_name)
        exec_time = parse_time(logs) if success else None
        result_file = out_dir / interference / f"{benchmark}.txt"
        result_file.parent.mkdir(parents=True, exist_ok=True)

        save_result(
            result_file,
            benchmark,
            interference,
            success,
            exec_time,
            logs,
        )

        if exec_time is not None:
            log(f"Execution time: {exec_time:.2f}s")

        return exec_time

    finally:
        cleanup()


def write_csv(results, csv_path):
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "benchmark",
                "interference",
                "execution_time_s",
                "success",
            ],
        )

        writer.writeheader()
        writer.writerows(results)


def run_all(run_id, resume=False):
    out_dir = Path(f"part2a-experiments/run{run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    total = len(BENCHMARKS) * len(INTERFERENCE)

    log(f"Starting run {run_id}")
    log(f"Total experiments: {total}")

    label_node()

    results = []

    current = 0

    for interference in INTERFERENCE:
        for benchmark in BENCHMARKS:
            current += 1

            log(f"[{current}/{total}]")

            result_file = out_dir / interference / f"{benchmark}.txt"

            if resume and result_file.exists():
                log("Skipping existing result")
                continue

            exec_time = run_experiment(
                benchmark,
                interference,
                out_dir,
            )

            results.append({
                "benchmark": benchmark,
                "interference": interference,
                "execution_time_s": exec_time,
                "success": exec_time is not None,
            })

            write_csv(results, csv_path)

    log(f"Run {run_id} finished")


def delete_cluster():
    log("Deleting cluster...")

    run([
        "kops",
        "delete",
        "cluster",
        CLUSTER_NAME,
        "--yes",
    ], check=False)


def main():
    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument("--run", type=int)
    group.add_argument("--runs", nargs="+", type=int)

    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--delete-cluster-after", action="store_true")

    args = parser.parse_args()

    runs = [args.run] if args.run else args.runs

    try:
        for i, run_id in enumerate(runs):
            run_all(run_id, args.resume)

            is_last = i == len(runs) - 1

            if args.delete_cluster_after and is_last:
                delete_cluster()

        log("All done")

    except KeyboardInterrupt:
        log("Interrupted")
        cleanup()

        if args.delete_cluster_after:
            delete_cluster()

        sys.exit(1)


if __name__ == "__main__":
    main()