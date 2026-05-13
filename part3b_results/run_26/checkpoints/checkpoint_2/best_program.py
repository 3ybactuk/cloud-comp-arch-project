from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any


# EVOLVE-BLOCK-START
def get_policy() -> dict[str, Any]:
    # Strategy B: 2 waves
    # Wave 1: [streamcluster@node-a, freqmine@node-b, canneal@node-b]
    # Wave 2: [blackscholes@node-a, vips@node-b, barnes@node-a, radix@node-a]
    return {
        "memcached": {
            "node": "node-a-8core",
            "threads": 2,
        },
        "waves": [
            [
                {"job": "streamcluster", "node": "node-a-8core", "threads": 4},
                {"job": "freqmine",      "node": "node-b-4core", "threads": 2},
                {"job": "canneal",       "node": "node-b-4core", "threads": 2},
            ],
            [
                {"job": "blackscholes", "node": "node-a-8core", "threads": 2},
                {"job": "vips",         "node": "node-b-4core", "threads": 2},
                {"job": "barnes",       "node": "node-a-8core", "threads": 2},
                {"job": "radix",        "node": "node-a-8core", "threads": 4},
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