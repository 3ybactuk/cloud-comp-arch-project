#!/usr/bin/env python3

from pathlib import Path
import argparse
import re
import sys

import pandas as pd
import matplotlib.pyplot as plt


EXPECTED_CONFIGS = [
    "no-ibench",
    "ibench-cpu",
    "ibench-l1d",
    "ibench-l1i",
    "ibench-l2",
    "ibench-llc",
    "ibench-membw",
]

MCPERF_COLUMNS = [
    "type", "avg", "std", "min", "p5", "p10", "p50", "p67", "p75",
    "p80", "p85", "p90", "p95", "p99", "p999", "p9999", "QPS", "target"
]


def parse_cpu_stats(text: str):
    """
    Parses lines like:
    CPU Usage Stats (avg/min/max): 66.63%,1.00%,100.00%
    """
    match = re.search(
        r"CPU Usage Stats \(avg/min/max\):\s*"
        r"([0-9.]+)%,\s*([0-9.]+)%,\s*([0-9.]+)%",
        text,
    )
    if not match:
        return None, None, None

    return tuple(float(x) for x in match.groups())


def parse_mcperf_file(path: Path) -> pd.DataFrame:
    rows = []

    text = path.read_text(errors="replace")
    cpu_avg, cpu_min, cpu_max = parse_cpu_stats(text)

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if line.startswith("./mcperf"):
            continue

        if line.startswith("Warning"):
            continue

        if line.startswith("CPU Usage"):
            continue

        parts = line.split()

        if len(parts) != len(MCPERF_COLUMNS):
            continue

        if parts[0] != "read":
            continue

        rows.append(parts)

    if not rows:
        raise ValueError(f"No valid mcperf rows found in {path}")

    df = pd.DataFrame(rows, columns=MCPERF_COLUMNS)

    numeric_cols = MCPERF_COLUMNS[1:]
    df[numeric_cols] = df[numeric_cols].astype(float)

    # mcperf latency fields are in microseconds in your output.
    # The assignment graph wants milliseconds.
    df["p95_ms"] = df["p95"] / 1000.0
    df["avg_ms"] = df["avg"] / 1000.0
    df["p99_ms"] = df["p99"] / 1000.0

    df["cpu_avg_percent"] = cpu_avg
    df["cpu_min_percent"] = cpu_min
    df["cpu_max_percent"] = cpu_max
    df["cpu_warning"] = "Detected max cpu usage > 95%" in text

    return df


def collect_all_runs(root: Path) -> pd.DataFrame:
    run_dirs = sorted(
        [p for p in root.iterdir() if p.is_dir() and p.name.startswith("run")],
        key=lambda p: natural_run_key(p.name),
    )

    if not run_dirs:
        raise FileNotFoundError(f"No run directories found under {root}")

    all_rows = []

    for run_dir in run_dirs:
        run_name = run_dir.name

        for config in EXPECTED_CONFIGS:
            path = run_dir / f"{config}.txt"

            if not path.exists():
                print(f"Warning: missing file {path}", file=sys.stderr)
                continue

            df = parse_mcperf_file(path)
            df["run"] = run_name
            df["config"] = config
            df["source_file"] = str(path)
            all_rows.append(df)

    if not all_rows:
        raise RuntimeError("No mcperf data was parsed.")

    return pd.concat(all_rows, ignore_index=True)


def natural_run_key(name: str):
    match = re.search(r"(\d+)", name)
    if match:
        return int(match.group(1))
    return name


def summarize(raw: pd.DataFrame) -> pd.DataFrame:
    summary = (
        raw
        .groupby(["config", "target"], as_index=False)
        .agg(
            runs=("run", "nunique"),

            mean_qps=("QPS", "mean"),
            std_qps=("QPS", "std"),
            min_qps=("QPS", "min"),
            max_qps=("QPS", "max"),

            mean_p95_ms=("p95_ms", "mean"),
            std_p95_ms=("p95_ms", "std"),
            min_p95_ms=("p95_ms", "min"),
            max_p95_ms=("p95_ms", "max"),

            mean_avg_ms=("avg_ms", "mean"),
            mean_p99_ms=("p99_ms", "mean"),

            cpu_avg_percent=("cpu_avg_percent", "mean"),
            cpu_max_percent=("cpu_max_percent", "max"),
            any_cpu_warning=("cpu_warning", "max"),
        )
    )

    summary["std_qps"] = summary["std_qps"].fillna(0.0)
    summary["std_p95_ms"] = summary["std_p95_ms"].fillna(0.0)

    return summary.sort_values(["config", "target"])


def estimate_knee_for_config(df: pd.DataFrame) -> dict:
    """
    Heuristic knee detector.

    It estimates the first point where the slope of p95 latency vs achieved QPS
    becomes much larger than the early-curve slope.

    This is not a mathematical proof of the knee. It gives you a defensible
    starting point for the written analysis.
    """
    df = df.sort_values("mean_qps").reset_index(drop=True).copy()

    if len(df) < 4:
        return {
            "knee_qps": None,
            "knee_p95_ms": None,
            "method": "not enough points",
        }

    dx = df["mean_qps"].diff()
    dy = df["mean_p95_ms"].diff()

    slope = dy / dx
    slope = slope.replace([float("inf"), -float("inf")], pd.NA)

    df["slope_ms_per_qps"] = slope
    df["slope_ms_per_1k_qps"] = slope * 1000.0

    early = df["slope_ms_per_1k_qps"].iloc[1:4].dropna()

    if len(early) == 0:
        baseline_slope = df["slope_ms_per_1k_qps"].dropna().median()
    else:
        baseline_slope = early.median()

    # Absolute fallback avoids declaring a knee from tiny numerical noise.
    threshold = max(3.0 * baseline_slope, 0.05)

    candidates = df[
        (df["slope_ms_per_1k_qps"] > threshold)
        & (df["mean_p95_ms"] > 0.5)
    ]

    if not candidates.empty:
        row = candidates.iloc[0]
        method = (
            f"first slope > max(3x early slope, 0.05 ms per 1K QPS); "
            f"threshold={threshold:.4f}"
        )
    else:
        # Fallback: largest slope point.
        valid = df.dropna(subset=["slope_ms_per_1k_qps"])
        row = valid.loc[valid["slope_ms_per_1k_qps"].idxmax()]
        method = "fallback: largest observed slope"

    return {
        "knee_qps": row["mean_qps"],
        "knee_target": row["target"],
        "knee_p95_ms": row["mean_p95_ms"],
        "knee_slope_ms_per_1k_qps": row["slope_ms_per_1k_qps"],
        "method": method,
    }


def estimate_knees(summary: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for config, df_config in summary.groupby("config"):
        result = estimate_knee_for_config(df_config)
        result["config"] = config
        rows.append(result)

    knees = pd.DataFrame(rows)

    cols = [
        "config",
        "knee_qps",
        "knee_target",
        "knee_p95_ms",
        "knee_slope_ms_per_1k_qps",
        "method",
    ]

    return knees[cols].sort_values("config")


def plot_summary(summary: pd.DataFrame, output_dir: Path):
    fig, ax = plt.subplots(figsize=(8, 5))

    for config in EXPECTED_CONFIGS:
        df = summary[summary["config"] == config].sort_values("mean_qps")

        if df.empty:
            continue

        ax.errorbar(
            df["mean_qps"],
            df["mean_p95_ms"],
            xerr=df["std_qps"],
            yerr=df["std_p95_ms"],
            marker="o",
            linewidth=1.5,
            capsize=3,
            label=config,
        )

    ax.set_xlim(0, 80000)
    ax.set_ylim(0, 6)

    ax.set_xlabel("Achieved QPS")
    ax.set_ylabel("95th percentile latency (ms)")

    ax.grid(True, alpha=0.3)
    ax.legend(title="Configuration", fontsize=8)

    fig.tight_layout()

    pdf_path = output_dir / "part1_latency_vs_qps.pdf"
    png_path = output_dir / "part1_latency_vs_qps.png"

    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")

    plt.close(fig)

    return pdf_path, png_path


def main():
    parser = argparse.ArgumentParser(
        description="Process Part 1 mcperf results across runs and iBench configurations."
    )
    parser.add_argument(
        "--root",
        default="part1-experiments",
        help="Root directory containing run1, run2, run3, ...",
    )
    parser.add_argument(
        "--out",
        default="part1_processed",
        help="Output directory for CSVs and plots.",
    )

    args = parser.parse_args()

    root = Path(args.root)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw = collect_all_runs(root)
    summary = summarize(raw)
    knees = estimate_knees(summary)

    raw_path = output_dir / "part1_raw_points.csv"
    summary_path = output_dir / "part1_summary.csv"
    knees_path = output_dir / "part1_knees.csv"

    raw.to_csv(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    knees.to_csv(knees_path, index=False)

    pdf_path, png_path = plot_summary(summary, output_dir)

    print()
    print("Processed Part 1 results.")
    print(f"Raw parsed points: {raw_path}")
    print(f"Averaged summary:  {summary_path}")
    print(f"Knee estimates:    {knees_path}")
    print(f"PDF plot:          {pdf_path}")
    print(f"PNG plot:          {png_path}")

    print()
    print("Runs found per configuration:")
    print(
        raw.groupby("config")["run"]
        .nunique()
        .reindex(EXPECTED_CONFIGS)
        .to_string()
    )

    print()
    print("Estimated knees:")
    print(knees.to_string(index=False))


if __name__ == "__main__":
    main()
