import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

JOB_COLORS = {
    "memcached":     "#7f7f7f",
    "freqmine":      "#2ca02c",
    "canneal":       "#caab90",
    "blackscholes":  "#bb8624",
    "streamcluster": "#ba96db",
    "barnes":        "#669da2",
    "vips":          "#d62728",
    "radix":         "#24c0a3",
    "scheduler":     "#ffffff",
}
ALL_CORES = [0, 1, 2, 3]
SLO_MS = 0.8

def iso_to_unix(ts_str: str) -> float:
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_cores(s: str) -> list[int]:
    s = s.strip("[]")
    if not s:
        return []
    return [int(x.strip()) for x in s.split(",")]


def parse_jobs_file(path: Path):
    """Returns (t0_unix, list of (rel_t, event, job, args_str))."""
    events = []
    t0 = None
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        ts = iso_to_unix(parts[0])
        event, job = parts[1], parts[2]
        args = " ".join(parts[3:])
        if t0 is None:
            t0 = ts
        events.append((ts - t0, event, job, args))
    return t0, events


def build_gantt_segments(events):
    """
    Returns list of dicts:
      {job, core, t_start, t_end, paused}
    Replays the event log to reconstruct per-core job assignment.
    """
    # active_on_core[core] = {'job': name, 't_start': rel_t, 'paused': bool}
    active = {}
    job_cores: dict[str, set] = {}
    paused_jobs: set = set()
    segments = []

    def close_core(core, t_end):
        if core in active:
            s = active.pop(core)
            if s["t_end_override"] is not None:
                t_end = s["t_end_override"]
            segments.append({
                "job": s["job"], "core": core,
                "t_start": s["t_start"], "t_end": t_end,
                "paused": s["paused"],
            })

    for rel_t, event, job, args in events:
        if event == "start" and job == "scheduler":
            continue

        elif event == "end" and job == "scheduler":
            for core in list(active.keys()):
                close_core(core, rel_t)

        elif event == "start" and job not in ("scheduler",):
            cores = parse_cores(args.split()[0]) if args else []
            for core in cores:
                close_core(core, rel_t)
                active[core] = {"job": job, "t_start": rel_t,
                                 "paused": job in paused_jobs, "t_end_override": None}
            job_cores[job] = set(cores)

        elif event == "update_cores":
            new_cores = set(parse_cores(args))
            old_cores = job_cores.get(job, set())
            for core in old_cores - new_cores:
                close_core(core, rel_t)
            for core in new_cores - old_cores:
                close_core(core, rel_t)
                active[core] = {"job": job, "t_start": rel_t,
                                 "paused": job in paused_jobs, "t_end_override": None}
            job_cores[job] = new_cores

        elif event == "end" and job != "scheduler":
            for core in list(job_cores.get(job, set())):
                close_core(core, rel_t)
            job_cores.pop(job, None)
            paused_jobs.discard(job)

        elif event == "pause":
            paused_jobs.add(job)
            for core in job_cores.get(job, set()):
                if core in active:
                    active[core]["paused"] = True

        elif event == "unpause":
            paused_jobs.discard(job)
            for core in job_cores.get(job, set()):
                if core in active:
                    active[core]["paused"] = False

    return segments


def parse_mcperf_dynamic(path: Path, qps_interval: int):
    """
    Returns (ts_start_unix, rows_df_list) where each row is a dict with
    rel_t (seconds from ts_start), QPS, p95_ms.
    """
    text = path.read_text(errors="replace")

    ts_start = None
    m = re.search(r"Timestamp start:\s*(\d+)", text)
    if m:
        ts_start = int(m.group(1)) / 1000.0  # ms → s

    rows = []
    idx = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("read"):
            continue
        parts = line.split()
        if len(parts) < 17:
            continue
        try:
            qps = float(parts[16])
            p95_us = float(parts[12])
            target = float(parts[17]) if len(parts) > 17 else None
        except ValueError:
            continue
        rows.append({
            "idx": idx,
            "rel_t": idx * qps_interval,
            "qps": qps,
            "p95_ms": p95_us / 1000.0,
            "target": target,
        })
        idx += 1

    return ts_start, rows


def parse_cpu_file(path: Path):
    """
    Parse /proc/stat snapshots (from q1_sweep.sh or added monitoring).
    Returns list of {ts, core, util_pct}.
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
            vals = [int(m.group(k)) for k in range(2, 9)]
            total = sum(vals)
            idle_t = vals[3] + vals[4]
            if core in prev:
                dt = total - prev[core]["total"]
                di = idle_t - prev[core]["idle"]
                if dt > 0:
                    records.append({"ts": ts, "core": core,
                                    "util_pct": 100.0 * (dt - di) / dt})
            prev[core] = {"total": total, "idle": idle_t}
    return records


# ── plotting ──────────────────────────────────────────────────────────────────

def plot_run(run_num: int, results_dir: Path, out_dir: Path,
             qps_interval: int, label: str = "Q3"):
    jobs_path = results_dir / f"jobs_{run_num}.txt"
    mcperf_path = results_dir / f"mcperf_{run_num}.txt"
    cpu_path = results_dir / f"cpu_{run_num}.txt"

    if not jobs_path.exists() or not mcperf_path.exists():
        print(f"  run {run_num}: missing files, skipping")
        return

    t0_jobs, events = parse_jobs_file(jobs_path)
    segments = build_gantt_segments(events)
    mcperf_ts_start, mcperf_rows = parse_mcperf_dynamic(mcperf_path, qps_interval)
    cpu_records = parse_cpu_file(cpu_path) if cpu_path.exists() else []

    # Align mcperf to jobs timeline
    if mcperf_ts_start is not None and t0_jobs is not None:
        mcperf_offset = mcperf_ts_start - t0_jobs
    else:
        mcperf_offset = 0.0
    for r in mcperf_rows:
        r["rel_t_jobs"] = r["rel_t"] + mcperf_offset

    # x limits: full experiment window (scheduler end or last mcperf sample, whichever is later)
    t_sched_end = max((e[0] for e in events if e[1] == "end" and e[2] == "scheduler"), default=0)
    t_mcperf_end = max((r["rel_t_jobs"] for r in mcperf_rows), default=1800)
    t_max = max(t_sched_end, t_mcperf_end)
    t_max_min = t_max / 60

    has_cpu = len(cpu_records) > 0
    n_rows = 3 if has_cpu else 2
    height_ratios = [2, 1.5, 1] if has_cpu else [2, 1.5]

    fig, axes = plt.subplots(n_rows, 1, figsize=(14, 4 * n_rows),
                             sharex=True,
                             gridspec_kw={"height_ratios": height_ratios})
    if n_rows == 2:
        axes = list(axes) + [None]
    ax_gantt, ax_qps, ax_cpu = axes

    # ── subplot 1: Gantt ─────────────────────────────────────────────────────
    for seg in segments:
        job = seg["job"]
        color = JOB_COLORS.get(job, "#cccccc")
        alpha = 0.4 if seg["paused"] else 1.0
        ax_gantt.barh(
            seg["core"],
            (seg["t_end"] - seg["t_start"]) / 60,
            left=seg["t_start"] / 60,
            color=color, alpha=alpha,
            edgecolor="black", linewidth=0.4,
            height=0.7,
        )
        if seg["paused"]:
            mid = (seg["t_start"] + seg["t_end"]) / 2 / 60
            ax_gantt.text(mid, seg["core"], "⏸", ha="center", va="center",
                          fontsize=7, color="black")

    ax_gantt.set_yticks(ALL_CORES)
    ax_gantt.set_yticklabels([f"Core {c}" for c in ALL_CORES])
    ax_gantt.set_ylim(-0.5, 3.5)
    ax_gantt.set_ylabel("CPU core")
    ax_gantt.set_title(f"{label} Run {run_num} — core assignment")
    ax_gantt.grid(axis="x", alpha=0.3)

    # Legend
    legend_jobs = sorted({s["job"] for s in segments})
    patches = [mpatches.Patch(color=JOB_COLORS.get(j, "#ccc"), label=j)
               for j in legend_jobs]
    ax_gantt.legend(handles=patches, loc="upper right", fontsize=7,
                    ncol=min(4, len(patches)))

    # ── subplot 2: QPS + p95 ─────────────────────────────────────────────────
    ax2r = ax_qps.twinx()

    ts_m = [r["rel_t_jobs"] / 60 for r in mcperf_rows]
    qps_vals = [r["qps"] / 1000 for r in mcperf_rows]
    p95_vals = [r["p95_ms"] for r in mcperf_rows]

    ax_qps.plot(ts_m, qps_vals, color="#1f77b4", linewidth=1.5, label="QPS")
    ax_qps.set_ylabel("QPS (K)", color="#1f77b4")
    ax_qps.tick_params(axis="y", labelcolor="#1f77b4")
    ax_qps.set_ylim(bottom=0)

    ax2r.plot(ts_m, p95_vals, color="#d62728", linewidth=1.5,
              linestyle="--", label="p95 latency")
    ax2r.axhline(SLO_MS, color="black", linestyle=":", linewidth=1.2,
                 label=f"SLO {SLO_MS} ms")
    ax2r.set_ylabel("p95 latency (ms)", color="#d62728")
    ax2r.tick_params(axis="y", labelcolor="#d62728")
    ax2r.set_ylim(bottom=0)

    lines1, labs1 = ax_qps.get_legend_handles_labels()
    lines2, labs2 = ax2r.get_legend_handles_labels()
    ax_qps.legend(lines1 + lines2, labs1 + labs2, fontsize=8, loc="upper left")
    ax_qps.grid(alpha=0.3)

    # ── subplot 3: per-core CPU util ─────────────────────────────────────────
    if ax_cpu is not None:
        if has_cpu and t0_jobs is not None:
            for core in ALL_CORES:
                core_recs = [(r["ts"] - t0_jobs) / 60 for r in cpu_records
                             if r["core"] == core]
                core_util = [r["util_pct"] for r in cpu_records
                             if r["core"] == core]
                if core_recs:
                    ax_cpu.plot(core_recs, core_util, linewidth=1,
                                label=f"Core {core}",
                                color=plt.cm.tab10(core / 4))
            ax_cpu.set_ylabel("CPU util (%)")
            ax_cpu.set_ylim(0, 105)
            ax_cpu.legend(fontsize=8, loc="upper right")
            ax_cpu.grid(alpha=0.3)
        else:
            ax_cpu.text(0.5, 0.5, "CPU utilisation data not available\n"
                        "(add cpu monitoring to run_experiment.sh)",
                        ha="center", va="center", transform=ax_cpu.transAxes,
                        fontsize=9, color="gray")
            ax_cpu.set_ylabel("CPU util (%)")

    ax_gantt.set_xlim(0, t_max_min)
    fig.supxlabel("Time (minutes from scheduler start)", y=0.01)
    fig.tight_layout(rect=[0, 0.02, 1, 1])

    for ext in ("pdf", "png"):
        out_path = out_dir / f"{label.lower()}_run{run_num}.{ext}"
        fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"  {label}_run{run_num}.pdf/png")


def compute_slo_violations(mcperf_rows, events, slo_ms=SLO_MS):
    """
    SLO violation ratio during the time window
    [first batch job start, last batch job end].
    """
    batch_jobs = {"freqmine", "canneal", "blackscholes",
                  "streamcluster", "barnes", "vips", "radix"}
    starts = [t for t, ev, job, _ in events if ev == "start" and job in batch_jobs]
    ends   = [t for t, ev, job, _ in events if ev == "end"   and job in batch_jobs]
    if not starts or not ends:
        return None, None, None
    t_first = min(starts)
    t_last  = max(ends)

    window = [r for r in mcperf_rows
              if t_first <= r["rel_t_jobs"] / 60 * 60 <= t_last or True]
    # simpler: use all rows that fall in window by rel_t_jobs seconds
    window = [r for r in mcperf_rows
              if t_first <= r["rel_t_jobs"] <= t_last]
    if not window:
        window = mcperf_rows  # fallback
    violations = sum(1 for r in window if r["p95_ms"] > slo_ms)
    return violations, len(window), violations / len(window) if window else None


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="part4_q3_results",
                        help="Directory containing results_run1/, results_run2/, ...")
    parser.add_argument("--out", default="q3_plots")
    parser.add_argument("--interval", type=int, default=15,
                        help="mcperf qps_interval in seconds")
    parser.add_argument("--label", default="Q3",
                        help="Label prefix for output files (Q3 or Q4)")
    args = parser.parse_args()

    results_root = Path(args.results)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_dirs = sorted(results_root.glob("results_run*"))
    if not run_dirs:
        print(f"No results_run* directories found in {results_root}")
        return

    print(f"Found {len(run_dirs)} run(s). Plotting...")

    for run_dir in run_dirs:
        m = re.search(r"(\d+)$", run_dir.name)
        if not m:
            continue
        run_num = int(m.group(1))
        print(f"Run {run_num}:")
        plot_run(run_num, run_dir, out_dir, args.interval, args.label)

        # Also compute SLO violation ratio
        jobs_path = run_dir / f"jobs_{run_num}.txt"
        mcperf_path = run_dir / f"mcperf_{run_num}.txt"
        if jobs_path.exists() and mcperf_path.exists():
            t0, events = parse_jobs_file(jobs_path)
            mcperf_ts_start, mcperf_rows = parse_mcperf_dynamic(
                mcperf_path, args.interval)
            if mcperf_ts_start is not None and t0 is not None:
                offset = mcperf_ts_start - t0
                for r in mcperf_rows:
                    r["rel_t_jobs"] = r["rel_t"] + offset
            viol, total, ratio = compute_slo_violations(mcperf_rows, events)
            if ratio is not None:
                print(f"  SLO violations: {viol}/{total} = {ratio:.1%}")

    print(f"\nDone. Plots in {out_dir}/")


if __name__ == "__main__":
    main()