from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any


# EVOLVE-BLOCK-START
def get_policy() -> dict[str, Any]:
    # Cluster: node-a-8core (8 CPU, memcached uses 2 → 6 free), node-b-4core (4 CPU, batch only)
    # CPU budget: threads * 0.9 per job. Do not overcommit.
    # Job runtimes (native): freqmine~165s, canneal~115s, streamcluster~150s,
    #   vips~40s, blackscholes~46s, barnes~54s, radix~11s
    # freqmine/vips: high LLC pressure → prefer node-b or limit threads on node-a
    # streamcluster: memory-intensive → prefer node-a
    # canneal: does not scale past 2 threads
    # Best hand-crafted makespan: 378s
    return {
        "memcached": {
            "node": "node-a-8core",
            "threads": 2,
        },
        "waves": [
            [
                {"job": "blackscholes", "node": "node-a-8core", "threads": 2},
                {"job": "barnes",       "node": "node-b-4core", "threads": 2},
            ],
            [
                {"job": "streamcluster", "node": "node-a-8core", "threads": 4},
                {"job": "freqmine",      "node": "node-b-4core", "threads": 2},
            ],
            [
                {"job": "vips",    "node": "node-a-8core", "threads": 2},
                {"job": "canneal", "node": "node-b-4core", "threads": 2},
            ],
            [
                {"job": "radix", "node": "node-a-8core", "threads": 4},
            ],
        ],
    }
# EVOLVE-BLOCK-END


if __name__ == "__main__":
    # RUNNER_PATH env var lets the evaluator find runner.py when OpenEvolve copies this file to /tmp
    _runner_path = os.environ.get(
        "RUNNER_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py"),
    )
    _spec = importlib.util.spec_from_file_location("runner", _runner_path)
    _runner = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_runner)
    _runner.run_policy(get_policy())