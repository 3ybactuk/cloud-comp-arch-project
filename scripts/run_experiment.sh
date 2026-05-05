#!/usr/bin/env bash
# Usage: ./run_experiment.sh <RUN_NUM> <MEMCACHE_SERVER_IP> <AGENT_IP> [QPS_SEED] [QPS_INTERVAL]

set -euo pipefail

RUN_NUM=${1:?Usage: $0 RUN_NUM MEMCACHE_SERVER_IP AGENT_IP [SEED] [INTERVAL]}
MEMCACHED_IP=${2:?}
AGENT_IP=${3:?}
QPS_SEED=${4:-""}
QPS_INTERVAL=${5:-15}

MEM_SERVER="${MEMCACHE_SERVER_USER:-ubuntu}@${MEMCACHED_IP}"
MCPERF=~/memcache-perf-dynamic/mcperf
OUT="results_run${RUN_NUM}"

mkdir -p "$OUT"

echo "=== Run $RUN_NUM: seed='$QPS_SEED' interval=${QPS_INTERVAL}s ==="

echo "Loading memcached..."
$MCPERF -s "$MEMCACHED_IP" --loadonly

MCPERF_OPTS="-s $MEMCACHED_IP -a $AGENT_IP \
    --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 1800 \
    --qps_interval $QPS_INTERVAL --qps_min 5000 --qps_max 110000 --qps_seed $QPS_SEED"

echo "Running mcperf..."
$MCPERF $MCPERF_OPTS > "$OUT/mcperf_${RUN_NUM}.txt" 2>&1
echo "mcperf done."

echo "Collecting logs..."
scp \
    "${MEM_SERVER}:~/controller/log*.txt" \
    "${MEM_SERVER}:~/controller/controller_run${RUN_NUM}.log" \
    "$OUT/" 2>/dev/null || true

LATEST_LOG=$(ls -t "$OUT"/log*.txt 2>/dev/null | head -1)
if [ -n "$LATEST_LOG" ]; then
    cp "$LATEST_LOG" "$OUT/jobs_${RUN_NUM}.txt"
    echo "Job log: $OUT/jobs_${RUN_NUM}.txt"
fi

echo "Run $RUN_NUM complete. Results in $OUT/"
