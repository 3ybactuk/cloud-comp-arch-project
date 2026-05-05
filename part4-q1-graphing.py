import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

MCPERF_COLUMNS = [
    "type", "avg", "std", "min",
    "p5", "p10", "p50", "p67", "p75", "p80", "p85", "p90",
    "p95", "p99", "p999", "p9999",
    "QPS", "target", "ts_start", "ts_end",
]

SLO_MS = 0.8  # For Task1 d)

T_COLORS = {1: "#1f77b4", 2: "#ff7f0e", 3: "#2ca02c"}
C_STYLES = {1: "-", 2: "--", 3: ":"}
C_MARKERS = {1: "o", 2: "s", 3: "^"}

def parse_mcperf_file(path: Path) -> pd.DataFrame:
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("read"):
            continue
        parts = line.split()
        if len(parts) != len(MCPERF_COLUMNS):
            continue
        rows.append(parts)
    if not rows:
        raise ValueError(f"No data in {path}")
    df = pd.DataFrame(rows, columns=MCPERF_COLUMNS)
    df[MCPERF_COLUMNS[1:]] = df[MCPERF_COLUMNS[1:]].astype(float)
    df["p95_ms"] = df["p95"] / 1000.0
    # ts_start / ts_end are in milliseconds
    df["ts_start_s"] = df["ts_start"] / 1000.0
    df["ts_end_s"] = df["ts_end"] / 1000.0
    return df


def parse_cpu_file(path: Path) -> pd.DataFrame:
    """
    Parse /proc/stat snapshots captured by q1_sweep.sh.
    Returns DataFrame with columns: timestamp, core, util_pct
    where util_pct is fraction of time non-idle for that 1s interval.
    """
    text = path.read_text(errors="replace")
    blocks = re.split(r"(\d{10})\n", text)

    records = []
    prev: dict[int, dict] = {}

    i = 1
    while i < len(blocks) - 1:
        ts_str = blocks[i].strip()
        body = blocks[i + 1]
        i += 2

        try:
            ts = float(ts_str)
        except ValueError:
            continue

        for line in body.splitlines():
            m = re.match(
                r"cpu(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
                line,
            )
            if not m:
                continue
            core = int(m.group(1))
            user, nice, system, idle, iowait, irq, softirq = (
                int(m.group(k)) for k in range(2, 9)
            )
            total = user + nice + system + idle + iowait + irq + softirq
            idle_t = idle + iowait

            if core in prev:
                dt = total - prev[core]["total"]
                di = idle_t - prev[core]["idle"]
                if dt > 0:
                    records.append({
                        "ts": ts,
                        "core": core,
                        "util_pct": 100.0 * (dt - di) / dt,
                    })
            prev[core] = {"total": total, "idle": idle_t}

    return pd.DataFrame(records)


# data loading

def load_all(results_dir: Path):
    """Return (mcperf_df, cpu_df) with T, C, run columns."""
    mcperf_rows, cpu_rows = [], []

    for path in sorted(results_dir.glob("mcperf_T*_C*_run*.txt")):
        m = re.match(r"mcperf_T(\d+)_C(\d+)_run(\d+)\.txt", path.name)
        if not m:
            continue
        T, C, run = int(m[1]), int(m[2]), int(m[3])
        df = parse_mcperf_file(path)
        df["T"] = T
        df["C"] = C
        df["run"] = run
        mcperf_rows.append(df)

    for path in sorted(results_dir.glob("cpu_T*_C*_run*.txt")):
        m = re.match(r"cpu_T(\d+)_C(\d+)_run(\d+)\.txt", path.name)
        if not m:
            continue
        T, C, run = int(m[1]), int(m[2]), int(m[3])
        df = parse_cpu_file(path)
        if df.empty:
            continue
        df["T"] = T
        df["C"] = C
        df["run"] = run
        cpu_rows.append(df)

    mcperf = pd.concat(mcperf_rows, ignore_index=True)
    cpu = pd.concat(cpu_rows, ignore_index=True) if cpu_rows else pd.DataFrame()
    return mcperf, cpu


def summarise_mcperf(mcperf: pd.DataFrame) -> pd.DataFrame:
    """Average p95_ms and QPS across runs for each (T, C, target)."""
    return (
        mcperf
        .groupby(["T", "C", "target"], as_index=False)
        .agg(
            mean_qps=("QPS", "mean"),
            std_qps=("QPS", "std"),
            mean_p95_ms=("p95_ms", "mean"),
            std_p95_ms=("p95_ms", "std"),
            n_runs=("run", "nunique"),
        )
        .fillna({"std_qps": 0.0, "std_p95_ms": 0.0})
        .sort_values(["T", "C", "target"])
    )


def cpu_util_for_step(cpu_df: pd.DataFrame, T: int, C: int, run: int,
                      ts_start: float, ts_end: float) -> float:
    """
    Return summed CPU util (%) across cores 0..C-1 for a given mcperf step.
    Sum across C cores → range 0 .. C*100.
    """
    mask = (
        (cpu_df["T"] == T) & (cpu_df["C"] == C) & (cpu_df["run"] == run) &
        (cpu_df["core"] < C) &
        (cpu_df["ts"] >= ts_start) & (cpu_df["ts"] <= ts_end)
    )
    sub = cpu_df[mask]
    if sub.empty:
        return np.nan
    return sub.groupby("core")["util_pct"].mean().sum()


# Q1a: single latency vs QPS plot

def plot_q1a(summary: pd.DataFrame, out_dir: Path, n_runs: int):
    fig, ax = plt.subplots(figsize=(9, 5.5))

    for T in sorted(summary["T"].unique()):
        for C in sorted(summary["C"].unique()):
            df = summary[(summary["T"] == T) & (summary["C"] == C)].sort_values("mean_qps")
            if df.empty:
                continue
            label = f"T={T}, C={C}"
            ax.errorbar(
                df["mean_qps"] / 1000,
                df["mean_p95_ms"],
                xerr=df["std_qps"] / 1000,
                yerr=df["std_p95_ms"],
                label=label,
                color=T_COLORS[T],
                linestyle=C_STYLES[C],
                marker=C_MARKERS[C],
                linewidth=1.5,
                markersize=5,
                capsize=3,
            )

    ax.axhline(SLO_MS, color="red", linestyle="--", linewidth=1.2, label=f"SLO {SLO_MS} ms")
    ax.set_xlabel("Achieved QPS (K)", fontsize=11)
    ax.set_ylabel("p95 latency (ms)", fontsize=11)
    ax.set_title(f"Memcached p95 latency vs QPS — all T×C configs (avg over {n_runs} runs)")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=3, fontsize=8, title="Config")
    fig.tight_layout()

    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"q1a_latency_vs_qps.{ext}", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  q1a_latency_vs_qps.pdf/png")


# Q1d: dual-axis plots (p95 + CPU util) per C value 

def plot_q1d(mcperf: pd.DataFrame, cpu: pd.DataFrame, T_val: int, out_dir: Path):
    for C in [1, 2, 3]:
        sub = mcperf[(mcperf["T"] == T_val) & (mcperf["C"] == C)].copy()
        if sub.empty:
            print(f"  Q1d: no data for T={T_val} C={C}, skipping")
            continue

        # Per-step CPU util: average across runs
        util_rows = []
        for run in sub["run"].unique():
            rsub = sub[sub["run"] == run].sort_values("ts_start_s")
            for _, row in rsub.iterrows():
                u = cpu_util_for_step(cpu, T_val, C, run, row["ts_start_s"], row["ts_end_s"])
                util_rows.append({
                    "target": row["target"],
                    "QPS": row["QPS"],
                    "p95_ms": row["p95_ms"],
                    "cpu_util": u,
                })
        util_df = pd.DataFrame(util_rows)
        step_summary = (
            util_df.groupby("target", as_index=False)
            .agg(mean_qps=("QPS", "mean"), mean_p95=("p95_ms", "mean"),
                 mean_cpu=("cpu_util", "mean"))
            .sort_values("mean_qps")
        )

        fig, ax1 = plt.subplots(figsize=(8, 5))
        ax2 = ax1.twinx()

        # Left axis: p95 latency
        ax1.plot(step_summary["mean_qps"] / 1000, step_summary["mean_p95"],
                 color="#1f77b4", marker="o", linewidth=1.8, markersize=5, label="p95 latency")
        ax1.axhline(SLO_MS, color="red", linestyle=":", linewidth=1.4, label=f"SLO {SLO_MS} ms")
        ax1.set_xlabel("Achieved QPS (K)", fontsize=11)
        ax1.set_ylabel("p95 latency (ms)", color="#1f77b4", fontsize=11)
        ax1.tick_params(axis="y", labelcolor="#1f77b4")
        ax1.set_xlim(left=0, right=125)
        ax1.set_ylim(bottom=0)
        ax1.yaxis.set_minor_locator(mticker.AutoMinorLocator())

        # Right axis: CPU util
        max_util = C * 100
        if not step_summary["mean_cpu"].isna().all():
            ax2.plot(step_summary["mean_qps"] / 1000, step_summary["mean_cpu"],
                     color="#ff7f0e", marker="s", linestyle="--", linewidth=1.8,
                     markersize=5, label="CPU util")
        ax2.set_ylabel(f"CPU utilisation (%, max={max_util}%)", color="#ff7f0e", fontsize=11)
        ax2.tick_params(axis="y", labelcolor="#ff7f0e")
        ax2.set_ylim(0, max_util)

        # Combined legend
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=9, loc="upper left")

        ax1.set_title(f"Memcached T={T_val}, C={C} — p95 latency & CPU utilisation")
        ax1.grid(True, alpha=0.3)
        fig.tight_layout()

        for ext in ("pdf", "png"):
            fig.savefig(out_dir / f"q1d_C{C}.{ext}", dpi=200, bbox_inches="tight")
        plt.close(fig)
        print(f"  q1d_C{C}.pdf/png")

def main():
    parser = argparse.ArgumentParser(description="Plot Part 4 Q1 results.")
    parser.add_argument("--results", default="part4_q1_results",
                        help="Directory with mcperf_T*_C*_run*.txt files")
    parser.add_argument("--out", default="q1_plots",
                        help="Output directory for plots")
    parser.add_argument("--T-for-q1d", type=int, default=2,
                        help="Thread count T to use for Q1d plots (answer to Q1c)")
    args = parser.parse_args()

    results_dir = Path(args.results)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading data from {results_dir} ...")
    mcperf, cpu = load_all(results_dir)
    print(f"  mcperf rows: {len(mcperf)}, cpu rows: {len(cpu)}")

    summary = summarise_mcperf(mcperf)
    n_runs = int(mcperf["run"].nunique())

    print("Plotting Q1a ...")
    plot_q1a(summary, out_dir, n_runs)

    print(f"Plotting Q1d (T={args.T_for_q1d}) ...")
    plot_q1d(mcperf, cpu, args.T_for_q1d, out_dir)

    print(f"\nDone. Plots saved to {out_dir}/")


if __name__ == "__main__":
    main()
