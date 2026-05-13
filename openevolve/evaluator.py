#!/usr/bin/env python3
# evaluator for part 3b - runs the scheduler and scores it
# TODO maybe add more metrics later idk

from __future__ import annotations

import json
import subprocess
import sys

from openevolve.evaluation_result import EvaluationResult


def _zero_result(feedback: str) -> EvaluationResult:
    metrics = {
        "combined_score": 0.0,
        "combined": 0.0,
        "makespan_score": 0.0,
        "slo_factor": 0.0,
    }
    return EvaluationResult(metrics=metrics, artifacts={"feedback": feedback})


def evaluate(program_path: str) -> EvaluationResult:
    try:
        proc = subprocess.run(
            [sys.executable, program_path],
            capture_output=True,
            text=True,
            timeout=7200,
        )
    except subprocess.TimeoutExpired:
        return _zero_result("scheduler took too long (>2h), killed it")
    except Exception as e:
        return _zero_result(f"couldnt launch program: {e}")

    result = {}
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue  # keep looking

    if not result:
        stderr_tail = (proc.stderr or "")[-500:]
        return _zero_result(f"no json found in output\nstderr: {stderr_tail}")

    if not result.get("all_jobs_completed", False):
        err = result.get("error", "unknown reason")
        return _zero_result(f"not all jobs finished: {err}")

    makespan = float(result.get("makespan_s", 9999))
    violation_ratio = float(result.get("violation_ratio", 1.0))
    p95_max = float(result.get("p95_max_ms", 999.0))

    # slo score - drops to 0 once violation ratio hits 5%
    slo_factor = max(0.0, 1.0 - violation_ratio * 20.0)

    makespan_score = min(1.0, 378.0 / max(makespan, 1.0))

    # weighted combo: 80% slo-gated, 20% pure makespan
    # the 20% part gives a gradient even when slo is violated so evolution doesnt get stuck
    combined = round(0.8 * slo_factor * makespan_score + 0.2 * makespan_score, 4)

    feedback = (
        f"makespan={makespan:.0f}s  violation_ratio={violation_ratio:.3f}"
        f"  p95_max={p95_max:.2f}ms  slo_factor={slo_factor:.3f}"
        f"  makespan_score={makespan_score:.3f}  combined_score={combined:.4f}"
    )

    metrics = {
        "combined_score": combined,
        "combined": combined,
        "makespan_score": makespan_score,
        "slo_factor": slo_factor,
    }
    return EvaluationResult(metrics=metrics, artifacts={"feedback": feedback})