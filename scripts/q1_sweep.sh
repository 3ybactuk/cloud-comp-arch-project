#!/usr/bin/env bash
# Part 4 Task 1: measure memcached p95 latency vs QPS for all configs.
# Usage: ./q1_sweep.sh <INTERNAL_MEMCACHE_SERVER_IP> <INTERNAL_AGENT_IP>

set -euo pipefail

MEMCACHED_IP=${1:?Usage: $0 MEMCACHE_SERVER_IP AGENT_IP}
AGENT_IP=${2:?Usage: $0 MEMCACHE_SERVER_IP AGENT_IP}
MCPERF=~/memcache-perf-dynamic/mcperf
MEM_SERVER="${MEMCACHE_SERVER_USER:-ubuntu}@${MEMCACHED_IP}"
RUNS=3
OUT=part4_q1_results
MCPERF_SCAN="--noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 2 --scan 5000:125000:10000"

mkdir -p "$OUT"

for T in 1 2 3; do
    echo "threads T=$T"
    # Update -t line in memcached.conf and restart
    ssh "$MEM_SERVER" "
        sudo sed -i 's/^-t [0-9]*\$/-t $T/' /etc/memcached.conf
        grep -q '^-t' /etc/memcached.conf || echo '-t $T' | sudo tee -a /etc/memcached.conf
        sudo systemctl restart memcached
        sleep 3
    "

    $MCPERF -s "$MEMCACHED_IP" --loadonly
    echo "mcperf data loaded for T=$T"

    for C in 1 2 3; do
        echo "T=$T C=$C"

        CORES=$(seq -s, 0 $((C-1)))
        ssh "$MEM_SERVER" "
            MEM_PID=\$(systemctl show memcached --property=MainPID --value)
            sudo taskset -a -cp $CORES \$MEM_PID
        "
        sleep 2

        for RUN in $(seq 1 $RUNS); do
            OUTFILE="$OUT/mcperf_T${T}_C${C}_run${RUN}.txt"
            echo "  run $RUN → $OUTFILE"

            # Capture CPU util in background during the run
            CPU_FILE="$OUT/cpu_T${T}_C${C}_run${RUN}.txt"
            ssh "$MEM_SERVER" "
                while true; do
                    date +%s
                    grep -E '^cpu[0-3] ' /proc/stat
                    sleep 1
                done
            " > "$CPU_FILE" 2>/dev/null &
            CPU_PID=$!

            $MCPERF -s "$MEMCACHED_IP" -a "$AGENT_IP" $MCPERF_SCAN > "$OUTFILE" 2>&1

            kill $CPU_PID 2>/dev/null || true
            sleep 3
        done
    done
done

echo "Results in $OUT/"
