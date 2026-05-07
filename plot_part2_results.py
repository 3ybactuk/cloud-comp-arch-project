#!/usr/bin/env python3
"""Generate Part 2a/2b plots from existing run CSV files."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "plots"
OUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUT_DIR / "part2_conclusions.md"
TABLES_PATH = OUT_DIR / "part2_final_tables.md"

PART2A_RUNS = [ROOT / "part2a-experiments" / f"run{i}" / "results.csv" for i in (1, 2, 3)]
PART2B_RUNS = [ROOT / "part2b-experiments" / f"run{i}" / "results.csv" for i in (1, 2, 3)]


def _safe_mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], 0.0
    return mean(values), stdev(values)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def plot_part2a() -> dict[tuple[str, str], list[float]]:
    runs = [load_csv(p) for p in PART2A_RUNS]

    # slowdown_samples[(benchmark, interference)] = [run1_slowdown, run2_slowdown, ...]
    slowdown_samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    benchmarks: set[str] = set()
    interferences: set[str] = set()

    for run in runs:
        baseline_per_bench: dict[str, float] = {}
        for row in run:
            b = row["benchmark"]
            i = row["interference"]
            t = float(row["execution_time_s"])
            benchmarks.add(b)
            interferences.add(i)
            if i == "baseline":
                baseline_per_bench[b] = t

        for row in run:
            b = row["benchmark"]
            i = row["interference"]
            if b not in baseline_per_bench:
                continue
            t = float(row["execution_time_s"])
            slowdown_samples[(b, i)].append(t / baseline_per_bench[b])

    benchmarks_sorted = sorted(benchmarks)
    interferences_sorted = sorted(interferences)

    x = list(range(len(benchmarks_sorted)))
    width = 0.10
    fig, ax = plt.subplots(figsize=(14, 6.5))

    for idx, intr in enumerate(interferences_sorted):
        means = []
        stds = []
        for bench in benchmarks_sorted:
            m, s = _safe_mean_std(slowdown_samples[(bench, intr)])
            means.append(m)
            stds.append(s)
        offset = (idx - (len(interferences_sorted) - 1) / 2.0) * width
        ax.bar(
            [v + offset for v in x],
            means,
            yerr=stds,
            width=width,
            capsize=3,
            label=intr,
            alpha=0.9,
        )

    ax.set_title("Part 2a: Runtime slowdown vs baseline (3 runs, mean +/- sample std)")
    ax.set_ylabel("Slowdown (runtime / baseline runtime)")
    ax.set_xlabel("Benchmark")
    ax.set_xticks(x)
    ax.set_xticklabels(benchmarks_sorted, rotation=25)
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=4, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "part2a_slowdown.png", dpi=220)
    fig.savefig(OUT_DIR / "part2a_slowdown.pdf")
    plt.close(fig)

    # Example-like line plot with points + error bars.
    fig, ax = plt.subplots(figsize=(12, 6.5))
    for bench in benchmarks_sorted:
        y = []
        yerr = []
        for intr in interferences_sorted:
            m, s = _safe_mean_std(slowdown_samples[(bench, intr)])
            y.append(m)
            yerr.append(s)
        ax.errorbar(interferences_sorted, y, yerr=yerr, marker="o", capsize=3, linewidth=1.8, label=bench)
    ax.set_title("Part 2a: Slowdown by interference (3 runs, mean +/- sample std)")
    ax.set_xlabel("Interference type")
    ax.set_ylabel("Slowdown (runtime / baseline)")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25)
    ax.tick_params(axis="x", rotation=30)
    ax.legend(ncol=3, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "part2a_slowdown_lines.png", dpi=220)
    fig.savefig(OUT_DIR / "part2a_slowdown_lines.pdf")
    plt.close(fig)

    # Heatmap: rows=benchmarks, cols=interference, values=mean slowdown
    heat = np.zeros((len(benchmarks_sorted), len(interferences_sorted)))
    for bi, bench in enumerate(benchmarks_sorted):
        for ii, intr in enumerate(interferences_sorted):
            heat[bi, ii] = mean(slowdown_samples[(bench, intr)])

    fig, ax = plt.subplots(figsize=(11.5, 6.5))
    im = ax.imshow(heat, aspect="auto", cmap="viridis")
    ax.set_title("Part 2a heatmap: mean slowdown (3 runs)")
    ax.set_xlabel("Interference type")
    ax.set_ylabel("Benchmark")
    ax.set_xticks(range(len(interferences_sorted)))
    ax.set_xticklabels(interferences_sorted, rotation=30, ha="right")
    ax.set_yticks(range(len(benchmarks_sorted)))
    ax.set_yticklabels(benchmarks_sorted)
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Slowdown (runtime / baseline)")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "part2a_slowdown_heatmap.png", dpi=220)
    fig.savefig(OUT_DIR / "part2a_slowdown_heatmap.pdf")
    plt.close(fig)

    return slowdown_samples


def plot_part2b() -> dict[tuple[str, int], list[float]]:
    runs = [load_csv(p) for p in PART2B_RUNS]

    # runtime_samples[(benchmark, threads)] = [run1, run2, ...]
    runtime_samples: dict[tuple[str, int], list[float]] = defaultdict(list)
    benchmarks: set[str] = set()
    threads: set[int] = set()

    for run in runs:
        for row in run:
            b = row["benchmark"]
            t = int(row["threads"])
            rt = float(row["execution_time_s"])
            benchmarks.add(b)
            threads.add(t)
            runtime_samples[(b, t)].append(rt)

    benchmarks_sorted = sorted(benchmarks)
    threads_sorted = sorted(threads)

    # Plot 1: absolute runtime
    fig, ax = plt.subplots(figsize=(11.5, 6.5))
    for b in benchmarks_sorted:
        y = []
        yerr = []
        for t in threads_sorted:
            m, s = _safe_mean_std(runtime_samples[(b, t)])
            y.append(m)
            yerr.append(s)
        ax.errorbar(threads_sorted, y, yerr=yerr, marker="o", capsize=3, linewidth=2, label=b)

    ax.set_title("Part 2b: Runtime vs threads (3 runs, mean +/- sample std)")
    ax.set_xlabel("Threads")
    ax.set_ylabel("Execution time (s)")
    ax.set_xticks(threads_sorted)
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25)
    ax.legend(ncol=3, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "part2b_runtime_vs_threads.png", dpi=220)
    fig.savefig(OUT_DIR / "part2b_runtime_vs_threads.pdf")
    plt.close(fig)

    # Plot 2: speedup = T1 / Tn
    fig, ax = plt.subplots(figsize=(11.5, 6.5))
    for b in benchmarks_sorted:
        # Build speedup samples per thread from run-paired data:
        # speedup_run = runtime(b,1,run) / runtime(b,t,run)
        per_thread_speedups: dict[int, list[float]] = defaultdict(list)
        base = runtime_samples[(b, 1)]
        for t in threads_sorted:
            current = runtime_samples[(b, t)]
            for r in range(min(len(base), len(current))):
                per_thread_speedups[t].append(base[r] / current[r])

        y = []
        yerr = []
        for t in threads_sorted:
            m, s = _safe_mean_std(per_thread_speedups[t])
            y.append(m)
            yerr.append(s)
        ax.errorbar(threads_sorted, y, yerr=yerr, marker="o", capsize=3, linewidth=2, label=b)

    ax.plot(threads_sorted, threads_sorted, "--", color="gray", linewidth=1.2, label="ideal speedup")
    ax.set_title("Part 2b: Speedup vs threads (3 runs, mean +/- sample std)")
    ax.set_xlabel("Threads")
    ax.set_ylabel("Speedup (T1 / Tn)")
    ax.set_xticks(threads_sorted)
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25)
    ax.legend(ncol=3, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "part2b_speedup_vs_threads.png", dpi=220)
    fig.savefig(OUT_DIR / "part2b_speedup_vs_threads.pdf")
    plt.close(fig)

    # Heatmap: rows=benchmarks, cols=threads, values=mean speedup (T1/Tn)
    speedup_heat = np.zeros((len(benchmarks_sorted), len(threads_sorted)))
    for bi, b in enumerate(benchmarks_sorted):
        t1 = runtime_samples[(b, 1)]
        for ti, t in enumerate(threads_sorted):
            cur = runtime_samples[(b, t)]
            speedup_heat[bi, ti] = mean(x / y for x, y in zip(t1, cur))

    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    im = ax.imshow(speedup_heat, aspect="auto", cmap="magma")
    ax.set_title("Part 2b heatmap: mean speedup (3 runs)")
    ax.set_xlabel("Threads")
    ax.set_ylabel("Benchmark")
    ax.set_xticks(range(len(threads_sorted)))
    ax.set_xticklabels(threads_sorted)
    ax.set_yticks(range(len(benchmarks_sorted)))
    ax.set_yticklabels(benchmarks_sorted)
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Speedup (T1 / Tn)")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "part2b_speedup_heatmap.png", dpi=220)
    fig.savefig(OUT_DIR / "part2b_speedup_heatmap.pdf")
    plt.close(fig)

    return runtime_samples


def write_summary(part2a_samples: dict[tuple[str, str], list[float]], part2b_runtime: dict[tuple[str, int], list[float]]) -> None:
    benchmarks_2a = sorted({k[0] for k in part2a_samples})
    interferences = sorted({k[1] for k in part2a_samples})
    benchmarks_2b = sorted({k[0] for k in part2b_runtime})

    avg_slowdown_by_intr: dict[str, float] = {}
    for intr in interferences:
        vals = [mean(part2a_samples[(b, intr)]) for b in benchmarks_2a]
        avg_slowdown_by_intr[intr] = mean(vals)

    worst_pair = max(
        ((mean(vals), b, intr) for (b, intr), vals in part2a_samples.items()),
        key=lambda x: x[0],
    )

    speedup8: dict[str, float] = {}
    for b in benchmarks_2b:
        t1 = part2b_runtime[(b, 1)]
        t8 = part2b_runtime[(b, 8)]
        speedup8[b] = mean(x / y for x, y in zip(t1, t8))

    best_speedup = max(speedup8.items(), key=lambda x: x[1])
    worst_speedup = min(speedup8.items(), key=lambda x: x[1])

    lines = [
        "# Part 2 conclusions (auto-generated)",
        "",
        "## Part 2a: Interference sensitivity",
        "- Metric: slowdown = runtime_with_interference / runtime_baseline.",
        "- Each point uses 3 runs (mean +/- sample std).",
        "",
        "Average slowdown across all 7 benchmarks by interference:",
    ]
    for intr, val in sorted(avg_slowdown_by_intr.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"- {intr}: {val:.3f}x")
    lines += [
        "",
        f"Worst observed benchmark/interference pair: {worst_pair[1]} + {worst_pair[2]} = {worst_pair[0]:.3f}x slowdown.",
        "",
        "Interpretation: cache-related interference dominates. Overall impact ranking is: LLC > L1I > L1D > L2 > MemBW > CPU.",
        "",
        "## Part 2b: Parallel scaling",
        "- Metric: speedup(Tn) = T1 / Tn.",
        "- Each point uses 3 runs (mean +/- sample std).",
        "",
        "Speedup at 8 threads:",
    ]
    for b, s in sorted(speedup8.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"- {b}: {s:.2f}x")
    lines += [
        "",
        f"Best scaling at 8 threads: {best_speedup[0]} ({best_speedup[1]:.2f}x).",
        f"Weakest scaling at 8 threads: {worst_speedup[0]} ({worst_speedup[1]:.2f}x).",
        "",
        "Interpretation: workloads scale differently; parallelism policy should be workload-aware, not one-size-fits-all.",
    ]
    SUMMARY_PATH.write_text("\n".join(lines) + "\n")


def write_final_tables(part2a_samples: dict[tuple[str, str], list[float]], part2b_runtime: dict[tuple[str, int], list[float]]) -> None:
    benchmarks_2a = sorted({k[0] for k in part2a_samples})
    interferences = sorted({k[1] for k in part2a_samples})
    benchmarks_2b = sorted({k[0] for k in part2b_runtime})
    threads = sorted({k[1] for k in part2b_runtime})

    # Recover absolute runtimes for part2a from raw CSV files.
    part2a_runtime: dict[tuple[str, str], list[float]] = defaultdict(list)
    for p in PART2A_RUNS:
        for row in load_csv(p):
            part2a_runtime[(row["benchmark"], row["interference"])].append(float(row["execution_time_s"]))

    lines = [
        "# Final tables for Part 2",
        "",
        "All metrics are computed from 3 runs.",
        "",
        "## Part 2a: runtime table in seconds (mean +/- std)",
        "",
    ]
    header = "| Benchmark | " + " | ".join(interferences) + " |"
    sep = "|" + "---|" * (len(interferences) + 1)
    lines.extend([header, sep])
    for b in benchmarks_2a:
        cells = [b]
        for intr in interferences:
            m, s = _safe_mean_std(part2a_runtime[(b, intr)])
            cells.append(f"{m:.3f} +/- {s:.3f}")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Part 2a: slowdown table (mean +/- std)",
        "",
        "Slowdown is normalized by baseline runtime of the same benchmark; therefore baseline is exactly 1.000.",
        "",
    ]
    header = "| Benchmark | " + " | ".join(interferences) + " |"
    sep = "|" + "---|" * (len(interferences) + 1)
    lines.extend([header, sep])
    for b in benchmarks_2a:
        cells = [b]
        for intr in interferences:
            m, s = _safe_mean_std(part2a_samples[(b, intr)])
            cells.append(f"{m:.3f} +/- {s:.3f}")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Part 2b: runtime table in seconds (mean +/- std)",
        "",
    ]
    header = "| Benchmark | " + " | ".join(f"t={t}" for t in threads) + " |"
    sep = "|" + "---|" * (len(threads) + 1)
    lines.extend([header, sep])
    for b in benchmarks_2b:
        cells = [b]
        for t in threads:
            m, s = _safe_mean_std(part2b_runtime[(b, t)])
            cells.append(f"{m:.3f} +/- {s:.3f}")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Part 2b: speedup table (T1/Tn, mean +/- std)",
        "",
    ]
    header = "| Benchmark | " + " | ".join(f"t={t}" for t in threads) + " |"
    sep = "|" + "---|" * (len(threads) + 1)
    lines.extend([header, sep])
    for b in benchmarks_2b:
        t1 = part2b_runtime[(b, 1)]
        cells = [b]
        for t in threads:
            cur = part2b_runtime[(b, t)]
            sp = [x / y for x, y in zip(t1, cur)]
            m, s = _safe_mean_std(sp)
            cells.append(f"{m:.3f} +/- {s:.3f}")
        lines.append("| " + " | ".join(cells) + " |")

    TABLES_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    plt.rcParams.update({
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "legend.fontsize": 9,
    })
    part2a_samples = plot_part2a()
    part2b_runtime = plot_part2b()
    write_summary(part2a_samples, part2b_runtime)
    write_final_tables(part2a_samples, part2b_runtime)
    print(f"Plots written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
