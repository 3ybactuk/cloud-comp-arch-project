#!/usr/bin/env python3
# python run_part2b.py --run 1
# python run_part2b.py --runs 1 2 3 --delete-cluster-after

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
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

THREADS = [1, 2, 4, 8]

PARSEC_DIR = "parsec-benchmarks/part2b"
CLUSTER_NAME = "part2b.k8s.local"

POLL_TIME = 15
TIMEOUT = 60 * 60


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
    result = kubectl(["get", "nodes", "-o", "wide"], capture=True)

    for line in result.stdout.splitlines():
        if "parsec" not in line.lower():
            continue

        node = line.split()[0]

        log(f"Found parsec node: {node}")

        kubectl([
            "label",
            "nodes",
            node,
            "cca-project-nodetype=parsec",
            "--overwrite",
        ])

        return

    log("Parsec node not found")


def patch_yaml(benchmark, threads):
    original = Path(PARSEC_DIR) / f"parsec-{benchmark}.yaml"

    if not original.exists():
        raise FileNotFoundError(original)

    content = original.read_text()

    patched = re.sub(
        r"(-n\s+)\d+",
        lambda m: m.group(1) + str(threads),
        content,
    )

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        prefix=f"{benchmark}-t{threads}-",
        delete=False,
    )

    tmp.write(patched)
    tmp.close()

    return tmp.name


def start_benchmark(benchmark, threads):
    log(f"Running {benchmark} ({threads} threads)")

    tmp_yaml = patch_yaml(benchmark, threads)

    try:
        kubectl(["create", "-f", tmp_yaml])
    finally:
        os.unlink(tmp_yaml)


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
            log(f"{job_name} done ({elapsed}s)")
            return True

        if "Failed" in status:
            log(f"{job_name} failed")
            return False

        log(f"Still running... {elapsed}s")

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

    logs = kubectl(
        ["logs", pod],
        capture=True,
        check=False,
    )

    return logs.stdout


def parse_time(logs):
    patterns = [
        r"[Ee]xecution\s+time[:\s]+([\d.]+)s?",
        r"real\s+(\d+)m([\d.]+)s",
        r"[Ee]lapsed\s+time[:\s]+([\d.]+)",
        r"real\s+([\d.]+)",
    ]

    m = re.search(patterns[0], logs)
    if m:
        return float(m.group(1))

    m = re.search(patterns[1], logs)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))

    for p in patterns[2:]:
        m = re.search(p, logs)
        if m:
            return float(m.group(1))

    return None


def save_result(path, benchmark, threads, success, exec_time, logs):
    with open(path, "w") as f:
        f.write(f"benchmark: {benchmark}\n")
        f.write(f"threads: {threads}\n")
        f.write(f"success: {success}\n")
        f.write(f"execution_time_s: {exec_time}\n")
        f.write(f"time: {datetime.now().isoformat()}\n")

        f.write("\n--- logs ---\n")
        f.write(logs)


def run_experiment(benchmark, threads, out_dir):
    log("=" * 50)
    log(f"{benchmark} / threads={threads}")

    cleanup()

    try:
        start_benchmark(benchmark, threads)

        job_name = f"parsec-{benchmark}"
        success = wait_for_job(job_name)
        logs = get_logs(job_name)
        exec_time = parse_time(logs) if success else None
        out_file = out_dir / f"{benchmark}_t{threads}.txt"

        save_result(
            out_file,
            benchmark,
            threads,
            success,
            exec_time,
            logs,
        )

        if exec_time is not None:
            log(f"Execution time: {exec_time:.2f}s")

        return exec_time

    finally:
        cleanup()


def write_csv(results, path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "benchmark",
                "threads",
                "execution_time_s",
                "success",
            ],
        )

        writer.writeheader()
        writer.writerows(results)


def run_all(run_id, resume=False):
    out_dir = Path(f"part2b-experiments/run{run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    total = len(BENCHMARKS) * len(THREADS)

    log(f"Starting run {run_id}")
    log(f"Total experiments: {total}")

    label_node()

    results = []
    current = 0

    for benchmark in BENCHMARKS:
        for threads in THREADS:
            current += 1

            log(f"[{current}/{total}]")

            result_file = out_dir / f"{benchmark}_t{threads}.txt"

            if resume and result_file.exists():
                log("Skipping existing result")
                continue

            exec_time = run_experiment(
                benchmark,
                threads,
                out_dir,
            )

            results.append({
                "benchmark": benchmark,
                "threads": threads,
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

            last = i == len(runs) - 1

            if args.delete_cluster_after and last:
                delete_cluster()

        log("All runs finished")

    except KeyboardInterrupt:
        log("Interrupted")
        cleanup()

        if args.delete_cluster_after:
            delete_cluster()

        sys.exit(1)


if __name__ == "__main__":
    main()