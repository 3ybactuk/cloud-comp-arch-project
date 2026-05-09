# Part 3 Policy Playbook

This document captures the agreed approach for Part 3:

- run many hand-crafted policy candidates once (screening),
- discard weak candidates early,
- run full 3 repetitions only for policies that pass strict gates.

## Objective Recap

For Part 3, the policy must:

1. Minimize total batch makespan (7 jobs).
2. Keep memcached SLO: p95 latency `< 1 ms` at `~30K QPS`.
3. Ensure all 7 jobs complete successfully.
4. Use cluster resources efficiently over time.

## Why This Screening Strategy

Full 3-run evaluation for every policy is expensive. We first run each policy once, then only promote strong candidates.

This is valid for report narrative as long as final reported policy metrics come from 3 runs.

## Screening Gates (Run #1)

A policy passes screening only if all conditions hold:

- **Validity gate:** all 7 PARSEC jobs completed (no failed/timeout/OOM).
- **30K-QPS coverage gate:** enough points near target throughput during the batch window.
  - Recommended threshold: at least `80%` of samples in `[29K, 31K]` achieved QPS.
- **SLO gate:** SLO violation ratio (on near-30K samples only) is low.
  - Recommended threshold: `<= 1%`.
  - Violation ratio definition:
    - numerator: number of samples with `p95 > 1ms`
    - denominator: total near-30K samples in batch window
- **Stability gate:** no long continuous violation stretch (recommended: no run of `> 60s` with `p95 > 1ms`).
- **Efficiency sanity gate:** makespan not obviously dominated by other candidates (soft gate for ranking, not hard reject if SLO is excellent).

If a policy fails any hard gate, do not run repetitions #2 and #3.

## Practical Notes from Part 1 + Part 2

- Memcached is sensitive to CPU/L1I/L2/LLC pressure.
- From Part 2 interference profile:
  - higher risk near memcached: `barnes`, `freqmine`, `vips`
  - lower risk candidates: `radix`, `streamcluster` (still monitor LLC effects)
- From Part 2 parallel scaling:
  - strong scalability: `radix`, `barnes`, `freqmine`
  - weaker late scaling: `blackscholes`, `canneal`, `vips`
- Implication:
  - keep risky cache-heavy phases away from memcached or reduce their threads,
  - use wave scheduling to keep utilization high without sustained p95 spikes.

## Report Tips (Part 3a)

For the "hand-crafted policy" section:

- Clearly state the final selected policy and why it was selected.
- Mention that multiple candidates were screened with run #1 gates to control cloud cost.
- Include one short paragraph comparing selected vs second-best candidate.
- In the "files modified" answer, include:
  - policy config file,
  - run script,
  - any generated Kubernetes YAML manifests (if stored).

## Submission Checklist (Part 3)

- `part_3_1_results_group_XXX/`:
  - `pods_1.json`, `pods_2.json`, `pods_3.json`
  - `mcperf_1.txt`, `mcperf_2.txt`, `mcperf_3.txt`
- Ensure files correspond exactly to numbers in the report.
- Keep raw logs for debugging and traceability.

## Next-Step Plan

1. Run screening (`python3 run_part3a.py`); pass `--delete-cluster-after` when you want the cluster removed after a full successful run **or** after an interrupt / uncaught exception (same idea as `run_part2a.py`).
2. Rank by:
   1) SLO compliance,
   2) violation ratio,
   3) makespan.
3. Tomorrow: run #2 and #3 only for shortlisted policy/policies.
