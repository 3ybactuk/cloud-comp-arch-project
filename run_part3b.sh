#!/usr/bin/env bash
# Run OpenEvolve for Part 3b.
# Prerequisites: cluster deployed, openevolve installed (pip install openevolve)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OE_DIR="$SCRIPT_DIR/openevolve"
# Next free part3b_results/run_N (override with PART3B_OUT=/exact/path).
RESULTS_BASE="$SCRIPT_DIR/part3b_results"
if [[ -n "${PART3B_OUT:-}" ]]; then
  OUT_DIR="$PART3B_OUT"
else
  mkdir -p "$RESULTS_BASE"
  n=1
  while [[ -e "$RESULTS_BASE/run_$n" ]]; do
    ((n += 1))
  done
  OUT_DIR="$RESULTS_BASE/run_$n"
fi

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY (Swiss AI key) before running}"
export RUNNER_PATH="$OE_DIR/runner.py"
export GCP_PROJECT="${GCP_PROJECT:-cca-eth-2026-group-087}"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import openevolve" 2>/dev/null; then
  echo "OpenEvolve not installed for $PYTHON. Run: $PYTHON -m pip install openevolve" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

echo "Output dir: $OUT_DIR"
echo "Starting OpenEvolve..."

exec "$PYTHON" -m openevolve.cli \
  --config "$OE_DIR/config.yaml" \
  -o "$OUT_DIR" \
  "$OE_DIR/policy.py" \
  "$OE_DIR/evaluator.py"
