"""
plot_part3a.py  –  Generate Part 3a plots for the CCA report.

For each of the 3 runs of policy p02_balanced_waves we produce one figure with
three stacked panels:

  1. p95 latency bar chart (y-axis = ms, x-axis = time relative to first
     batch-job start).  Each bar spans [ts_start, ts_end] from mcperf and is
     shaded by which batch job is active on node-a at that moment.
  2. Gantt timeline for node-a-8core (memcached + batch jobs).
  3. Gantt timeline for node-b-4core (batch jobs only).

Usage:
    python3 plot_part3a.py
Outputs are written to plots/part3a_run{1,2,3}.{pdf,png}.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ── config ────────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent
SCREENING = BASE / "part3-screening" / "p02_balanced_waves"
OUT_DIR   = BASE / "plots"
OUT_DIR.mkdir(exist_ok=True)

SLO_MS = 1.0

JOB_COLORS = {
    "memcached":     "#7f7f7f",
    "freqmine":      "#2ca02c",
    "canneal":       "#caab90",
    "blackscholes":  "#bb8624",
    "streamcluster": "#ba96db",
    "barnes":        "#669da2",
    "vips":          "#d62728",
    "radix":         "#24c0a3",
    "idle":          "#eeeeee",
}

MCPERF_COLS = [
    "type","avg","std","min",
    "p5","p10","p50","p67","p75","p80","p85","p90",
    "p95","p99","p999","p9999",
    "QPS","target","ts_start","ts_end",
]

# Thread counts per job per node (from policy spec)
THREAD_COUNTS = {
    "radix": 4, "streamcluster": 4, "vips": 2,          # node-a
    "blackscholes": 2, "canneal": 2, "freqmine": 2, "barnes": 2,  # node-b
    "memcached": 1,
}

# ── parsers ────────────────────────────────────────────────────────────────────

def iso_to_unix(s: str) -> float:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.timestamp()


def parse_mcperf(path: Path):
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("read"):
            continue
        parts = line.split()
        if len(parts) != len(MCPERF_COLS):
            continue
        d = dict(zip(MCPERF_COLS, parts))
        rows.append({
            "ts_start_s": float(d["ts_start"]) / 1000.0,
            "ts_end_s":   float(d["ts_end"])   / 1000.0,
            "p95_ms":     float(d["p95"])       / 1000.0,
            "qps":        float(d["QPS"]),
        })
    return rows


def parse_pods(path: Path):
    data = json.loads(path.read_text())
    jobs = {}
    for item in data.get("items", []):
        name = item["metadata"]["name"]
        node = item["spec"].get("nodeName", "unknown")
        start_str = item["status"].get("startTime", "")
        cs = item["status"].get("containerStatuses", [{}])
        terminated = cs[0].get("state", {}).get("terminated", {}) if cs else {}
        end_str = terminated.get("finishedAt", "")

        if not start_str:
            continue

        start = iso_to_unix(start_str)
        end   = iso_to_unix(end_str) if end_str else None

        # Determine job type from pod name
        job = "unknown"
        for j in ["barnes","blackscholes","canneal","freqmine","radix","streamcluster","vips","memcached"]:
            if j in name:
                job = j
                break

        jobs[name] = {"job": job, "node": node, "start": start, "end": end}
    return jobs


# ── helpers ────────────────────────────────────────────────────────────────────

def jobs_active_at(t: float, job_list):
    """Return list of job dicts active at unix timestamp t."""
    return [j for j in job_list if j["start"] <= t <= (j["end"] or t + 1)]


def dominant_color_on_node(t: float, job_list, node_key: str):
    """Return color of first batch job active at time t on the given node."""
    BATCH = {"barnes","blackscholes","canneal","freqmine","radix","streamcluster","vips"}
    active = [j for j in job_list
              if node_key in j["node"]
              and j["job"] in BATCH
              and j["start"] <= t <= (j["end"] or t + 1)]
    if active:
        # pick the one that started latest (most recent)
        best = max(active, key=lambda j: j["start"])
        return JOB_COLORS[best["job"]]
    return JOB_COLORS["memcached"]


# ── build virtual core segments for Gantt ─────────────────────────────────────

def build_gantt_segments(job_list, node_key: str, n_cores: int, t0: float):
    """
    Assign each batch job to virtual core rows based on thread count.
    memcached is pinned to core 0 on node-a.
    Returns list of dicts: {job, core, t_start_rel, t_end_rel}
    """
    BATCH = {"barnes","blackscholes","canneal","freqmine","radix","streamcluster","vips"}
    node_jobs = [j for j in job_list if node_key in j["node"]]

    segments = []
    is_node_a = "8core" in node_key

    # memcached uses 2 threads on node-a → cores 0-1
    MEM_THREADS = 2
    if is_node_a:
        mc = next((j for j in node_jobs if j["job"] == "memcached"), None)
        if mc:
            t_mc_start = mc["start"] - t0
            t_mc_end   = (mc["end"] or mc["start"] + 600) - t0
            for c in range(MEM_THREADS):
                segments.append({
                    "job": "memcached", "core": c,
                    "t_start_rel": t_mc_start,
                    "t_end_rel": t_mc_end,
                })

    first_core = MEM_THREADS if is_node_a else 0

    for j in sorted(node_jobs, key=lambda x: x["start"]):
        if j["job"] not in BATCH:
            continue
        threads = THREAD_COUNTS.get(j["job"], 1)
        t_start_rel = j["start"] - t0
        t_end_rel   = (j["end"] or j["start"] + 1) - t0
        for c in range(first_core, first_core + threads):
            if c < n_cores:
                segments.append({
                    "job": j["job"], "core": c,
                    "t_start_rel": t_start_rel,
                    "t_end_rel": t_end_rel,
                })

    return segments


# ── plot ──────────────────────────────────────────────────────────────────────

def plot_run(run_dir: Path, run_num: int):
    mcperf_rows = parse_mcperf(run_dir / "mcperf_1.txt")
    pod_data    = parse_pods(run_dir   / "pods_1.json")

    job_list = list(pod_data.values())
    batch_jobs = [j for j in job_list if j["job"] not in ("memcached", "unknown")]

    if not batch_jobs or not mcperf_rows:
        print(f"run{run_num}: missing data, skipping")
        return

    # t0 = start of first batch container
    t0 = min(j["start"] for j in batch_jobs)
    # end of last batch container
    t_end_abs = max(j["end"] for j in batch_jobs if j["end"])

    def rel(t): return (t - t0) / 60.0  # relative time in minutes

    # mcperf x-axis in relative minutes
    for r in mcperf_rows:
        r["x_start"] = (r["ts_start_s"] - t0) / 60.0
        r["x_end"]   = (r["ts_end_s"]   - t0) / 60.0
        r["x_mid"]   = (r["x_start"] + r["x_end"]) / 2.0

    x_max = (t_end_abs - t0) / 60.0 + 0.5
    x_min = min(r["x_start"] for r in mcperf_rows) - 0.2

    # ── build gantt data ──────────────────────────────────────────────────────
    segs_a = build_gantt_segments(job_list, "8core", 8, t0)
    segs_b = build_gantt_segments(job_list, "4core", 4, t0)

    # ── figure ────────────────────────────────────────────────────────────────
    fig, (ax_lat, ax_a, ax_b) = plt.subplots(
        3, 1,
        figsize=(13, 9),
        gridspec_kw={"height_ratios": [2.5, 2, 1.5]},
    )
    fig.suptitle(
        f"Run {run_num} — p02 balanced-waves policy",
        fontsize=13, fontweight="bold", y=0.99,
    )

    # ── panel 1: p95 latency bar chart ────────────────────────────────────────
    BATCH = {"barnes","blackscholes","canneal","freqmine","radix","streamcluster","vips"}

    for r in mcperf_rows:
        width = max(r["x_end"] - r["x_start"], 0.001)
        color = dominant_color_on_node(r["ts_start_s"], job_list, "8core")
        ax_lat.bar(
            r["x_start"], r["p95_ms"],
            width=width, align="edge",
            color=color, edgecolor="none", alpha=0.85,
        )

    # Thin strip at the bottom of the p95 panel showing what runs on node-b
    STRIP_H = 0.04   # height in ms units
    y_strip  = -0.07
    ax_lat.set_ylim(y_strip - STRIP_H * 0.5,
                    max(1.5, max(r["p95_ms"] for r in mcperf_rows) * 1.2))

    nb_jobs = sorted(
        [j for j in job_list if "4core" in j["node"] and j["job"] in BATCH],
        key=lambda j: j["start"],
    )
    for j in nb_jobs:
        x_s = (j["start"] - t0) / 60.0
        x_e = ((j["end"] or j["start"] + 1) - t0) / 60.0
        ax_lat.barh(
            y_strip, x_e - x_s, left=x_s, height=STRIP_H,
            color=JOB_COLORS[j["job"]], edgecolor="none", alpha=0.9,
        )
    ax_lat.text(x_min + 0.05, y_strip, "node-b →", va="center",
                fontsize=6.5, color="#444")

    # Vertical dashed lines at wave boundaries (when first job of each wave starts)
    wave_starts = {}
    for j in sorted(batch_jobs, key=lambda j: j["start"]):
        rel_min = (j["start"] - t0) / 60.0
        # round to nearest unique start cluster
        key = round(rel_min, 1)
        wave_starts[key] = rel_min
    seen_starts = sorted(set(round((j["start"]-t0)/60.0, 1) for j in batch_jobs))
    for i, ws in enumerate(seen_starts):
        if ws > 0.05:   # skip t=0 itself
            ax_lat.axvline(ws, color="#aaaaaa", linewidth=0.8,
                           linestyle=":", zorder=0)

    ax_lat.axhline(SLO_MS, color="black", linewidth=1.2, linestyle="--",
                   label=f"SLO {SLO_MS} ms")
    ax_lat.axhline(0, color="#cccccc", linewidth=0.5)
    ax_lat.set_ylabel("p95 latency (ms)", fontsize=10)
    ax_lat.set_xlim(x_min, x_max)
    ax_lat.grid(axis="y", alpha=0.35)
    ax_lat.legend(fontsize=9, loc="upper right")
    ax_lat.set_title(
        "memcached p95 latency  (bar color = active job on node-a;  "
        "bottom strip = active job on node-b;  dotted lines = wave starts)",
        fontsize=8.5,
    )

    # ── panel 2: node-a gantt ─────────────────────────────────────────────────
    n_a_cores = 8
    for seg in segs_a:
        ax_a.barh(
            seg["core"],
            (seg["t_end_rel"] - seg["t_start_rel"]) / 60.0,
            left=seg["t_start_rel"] / 60.0,
            color=JOB_COLORS.get(seg["job"], "#cccccc"),
            edgecolor="black", linewidth=0.3, height=0.75,
        )
    ax_a.set_yticks(range(n_a_cores))
    ax_a.set_yticklabels([f"core {c}" for c in range(n_a_cores)], fontsize=8)
    ax_a.set_ylabel("node-a-8core", fontsize=9)
    ax_a.set_ylim(-0.5, n_a_cores - 0.5)
    ax_a.set_xlim(x_min, x_max)
    ax_a.grid(axis="x", alpha=0.3)
    ax_a.set_title("node-a-8core (e2-standard-8) — job → core assignment",
                   fontsize=9)

    # ── panel 3: node-b gantt ─────────────────────────────────────────────────
    n_b_cores = 4
    for seg in segs_b:
        ax_b.barh(
            seg["core"],
            (seg["t_end_rel"] - seg["t_start_rel"]) / 60.0,
            left=seg["t_start_rel"] / 60.0,
            color=JOB_COLORS.get(seg["job"], "#cccccc"),
            edgecolor="black", linewidth=0.3, height=0.75,
        )
    ax_b.set_yticks(range(n_b_cores))
    ax_b.set_yticklabels([f"core {c}" for c in range(n_b_cores)], fontsize=8)
    ax_b.set_ylabel("node-b-4core", fontsize=9)
    ax_b.set_ylim(-0.5, n_b_cores - 0.5)
    ax_b.set_xlim(x_min, x_max)
    ax_b.grid(axis="x", alpha=0.3)
    ax_b.set_title("node-b-4core (n2d-highcpu-4) — job → core assignment",
                   fontsize=9)
    ax_b.set_xlabel("Time (minutes from first batch-job start, t = 0)", fontsize=10)

    # ── shared legend — show [node] for each batch job ────────────────────────
    legend_entries = [
        ("memcached [node-a]",     "memcached"),
        ("radix [node-a]",         "radix"),
        ("streamcluster [node-a]", "streamcluster"),
        ("vips [node-a]",          "vips"),
        ("blackscholes [node-b]",  "blackscholes"),
        ("canneal [node-b]",       "canneal"),
        ("freqmine [node-b]",      "freqmine"),
        ("barnes [node-b]",        "barnes"),
    ]
    patches = [mpatches.Patch(color=JOB_COLORS[job], label=label)
               for label, job in legend_entries]
    fig.legend(handles=patches, loc="lower center", ncol=4,
               fontsize=8, bbox_to_anchor=(0.5, 0.0), framealpha=0.9)

    fig.tight_layout(rect=[0, 0.07, 1, 1])

    for ext in ("pdf", "png"):
        out = OUT_DIR / f"part3a_run{run_num}.{ext}"
        fig.savefig(out, dpi=180, bbox_inches="tight")
        print(f"  saved {out}")
    plt.close(fig)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    run_dirs = {
        1: SCREENING / "run1",
        2: SCREENING / "run2",
        3: SCREENING / "run3",
    }
    for run_num, run_dir in sorted(run_dirs.items()):
        if not run_dir.exists():
            print(f"run{run_num}: directory missing, skipping")
            continue
        print(f"Plotting run {run_num} …")
        plot_run(run_dir, run_num)
    print("Done.")


if __name__ == "__main__":
    main()