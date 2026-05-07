# Part 2: Unified Analysis (2a + 2b)

## 1) Experiment Purpose (Part 2 overall)

Part 2 is designed to characterize batch workloads along two dimensions that are critical for scheduling:

- **Interference sensitivity (Part 2a):** how much each workload slows down under different resource-contention sources.
- **Parallel scalability (Part 2b):** how much each workload benefits from additional threads (1, 2, 4, 8).

Together, these experiments provide the evidence needed for later scheduling decisions:
- which workloads should avoid colocating with specific noisy neighbors;
- which workloads deserve more cores because they scale well;
- which workloads should run with fewer cores because additional parallelism gives weak returns.

## 2) Part 2a: Meaning of each interference type

In Part 2a, each iBench workload stresses a different bottleneck:

- **ibench-cpu:** mostly core execution contention (compute pipeline pressure).
- **ibench-l1d:** pressure on L1 data cache (data locality disruption).
- **ibench-l1i:** pressure on L1 instruction cache (instruction stream disruption).
- **ibench-l2:** pressure on private/shared mid-level cache (working-set spillover).
- **ibench-llc:** pressure on last-level cache (cross-core shared cache contention).
- **ibench-membw:** memory-bandwidth pressure (DRAM channel contention).
- **baseline:** no interference (reference runtime).

Interpretation rule:
- slowdown > 1 means degradation relative to baseline;
- larger slowdown means stronger sensitivity to that bottleneck.

## 3) Part 2a Results and Detailed Interpretation

### Part 2a runtime table (seconds, mean +/- std)

| Benchmark | baseline | ibench-cpu | ibench-l1d | ibench-l1i | ibench-l2 | ibench-llc | ibench-membw |
|---|---|---|---|---|---|---|---|
| barnes | 150.555 +/- 8.335 | 221.452 +/- 1.835 | 260.472 +/- 9.463 | 304.924 +/- 7.846 | 267.337 +/- 3.261 | 374.586 +/- 10.716 | 261.940 +/- 15.680 |
| blackscholes | 0.876 +/- 0.016 | 1.134 +/- 0.034 | 1.197 +/- 0.021 | 1.441 +/- 0.125 | 1.265 +/- 0.064 | 1.252 +/- 0.025 | 1.156 +/- 0.013 |
| canneal | 11.641 +/- 2.489 | 13.315 +/- 0.471 | 17.064 +/- 1.407 | 15.694 +/- 0.474 | 16.444 +/- 0.774 | 20.991 +/- 0.473 | 15.041 +/- 0.582 |
| freqmine | 7.323 +/- 1.328 | 13.489 +/- 0.449 | 11.691 +/- 4.663 | 13.833 +/- 0.523 | 8.933 +/- 0.190 | 13.273 +/- 2.431 | 11.002 +/- 0.896 |
| radix | 62.809 +/- 1.615 | 70.849 +/- 4.204 | 84.287 +/- 4.568 | 72.663 +/- 3.179 | 80.107 +/- 0.172 | 104.962 +/- 6.523 | 81.579 +/- 9.124 |
| streamcluster | 12.962 +/- 1.857 | 15.829 +/- 2.553 | 15.868 +/- 0.265 | 17.003 +/- 0.216 | 16.975 +/- 2.641 | 20.725 +/- 1.139 | 14.654 +/- 0.254 |
| vips | 125.338 +/- 15.013 | 194.285 +/- 4.077 | 217.483 +/- 14.064 | 211.538 +/- 1.780 | 221.948 +/- 13.465 | 226.365 +/- 8.953 | 199.542 +/- 9.253 |

### Part 2a slowdown table (normalized by baseline, mean +/- std)

| Benchmark | baseline | ibench-cpu | ibench-l1d | ibench-l1i | ibench-l2 | ibench-llc | ibench-membw |
|---|---|---|---|---|---|---|---|
| barnes | 1.000 +/- 0.000 | 1.474 +/- 0.073 | 1.732 +/- 0.058 | 2.028 +/- 0.091 | 1.780 +/- 0.112 | 2.495 +/- 0.207 | 1.742 +/- 0.106 |
| blackscholes | 1.000 +/- 0.000 | 1.294 +/- 0.052 | 1.367 +/- 0.039 | 1.645 +/- 0.149 | 1.445 +/- 0.097 | 1.429 +/- 0.018 | 1.320 +/- 0.025 |
| canneal | 1.000 +/- 0.000 | 1.183 +/- 0.287 | 1.497 +/- 0.231 | 1.386 +/- 0.267 | 1.453 +/- 0.287 | 1.861 +/- 0.409 | 1.332 +/- 0.281 |
| freqmine | 1.000 +/- 0.000 | 1.873 +/- 0.254 | 1.649 +/- 0.780 | 1.926 +/- 0.311 | 1.246 +/- 0.218 | 1.874 +/- 0.595 | 1.541 +/- 0.345 |
| radix | 1.000 +/- 0.000 | 1.129 +/- 0.076 | 1.342 +/- 0.071 | 1.157 +/- 0.033 | 1.276 +/- 0.036 | 1.674 +/- 0.147 | 1.300 +/- 0.150 |
| streamcluster | 1.000 +/- 0.000 | 1.221 +/- 0.077 | 1.238 +/- 0.150 | 1.330 +/- 0.192 | 1.341 +/- 0.365 | 1.620 +/- 0.233 | 1.147 +/- 0.168 |
| vips | 1.000 +/- 0.000 | 1.563 +/- 0.160 | 1.756 +/- 0.281 | 1.703 +/- 0.188 | 1.793 +/- 0.291 | 1.817 +/- 0.138 | 1.608 +/- 0.212 |

### Part 2a key findings

- **Strongest interference overall:** LLC contention is worst on average.
- **Overall impact ranking (average over benchmarks):**
  **LLC > L1I > L1D > L2 > MemBW > CPU**.
- **Worst specific pair:** `barnes + ibench-llc = 2.495x` slowdown.
- **Most sensitive workloads (broadly):** `barnes`, `vips`, and `freqmine`.
- **Relatively resilient workload:** `radix` (still degrades, but less than the most sensitive jobs).

Scheduling implication from 2a:
- avoid colocating cache-sensitive jobs with cache-heavy interference;
- if colocated, prefer combinations with lower mutual cache pressure;
- LLC contention should be treated as high-risk for throughput jobs.

## 4) Part 2b: Experiment Meaning

Part 2b measures how runtime changes with thread count (1, 2, 4, 8) on the 8-core node, without interference.
It answers: **which workloads convert extra cores into proportional speedup, and which do not**.

This quantifies diminishing returns and helps choose thread allocations in later scheduling.

## 5) Part 2b Results

### Part 2b runtime table (seconds, mean +/- std)

| Benchmark | t=1 | t=2 | t=4 | t=8 |
|---|---|---|---|---|
| barnes | 118.059 +/- 7.605 | 57.647 +/- 1.497 | 30.270 +/- 0.818 | 23.349 +/- 0.541 |
| blackscholes | 65.293 +/- 0.600 | 38.695 +/- 0.511 | 25.027 +/- 0.035 | 21.342 +/- 0.024 |
| canneal | 263.842 +/- 25.009 | 169.775 +/- 19.756 | 122.576 +/- 20.972 | 90.050 +/- 13.750 |
| freqmine | 341.578 +/- 5.635 | 173.839 +/- 3.128 | 88.815 +/- 1.707 | 69.094 +/- 1.404 |
| radix | 35.382 +/- 1.256 | 17.619 +/- 0.327 | 9.209 +/- 0.210 | 5.634 +/- 0.205 |
| streamcluster | 545.001 +/- 104.968 | 273.789 +/- 33.367 | 165.477 +/- 15.855 | 116.847 +/- 6.270 |
| vips | 61.068 +/- 0.945 | 35.637 +/- 5.972 | 16.840 +/- 0.051 | 15.896 +/- 2.755 |

### Part 2b speedup table (T1/Tn, mean +/- std)

| Benchmark | t=1 | t=2 | t=4 | t=8 |
|---|---|---|---|---|
| barnes | 1.000 +/- 0.000 | 2.047 +/- 0.081 | 3.898 +/- 0.152 | 5.053 +/- 0.211 |
| blackscholes | 1.000 +/- 0.000 | 1.687 +/- 0.020 | 2.609 +/- 0.020 | 3.059 +/- 0.025 |
| canneal | 1.000 +/- 0.000 | 1.557 +/- 0.040 | 2.173 +/- 0.180 | 2.951 +/- 0.223 |
| freqmine | 1.000 +/- 0.000 | 1.966 +/- 0.061 | 3.847 +/- 0.092 | 4.945 +/- 0.145 |
| radix | 1.000 +/- 0.000 | 2.008 +/- 0.037 | 3.842 +/- 0.055 | 6.280 +/- 0.058 |
| streamcluster | 1.000 +/- 0.000 | 1.979 +/- 0.145 | 3.281 +/- 0.429 | 4.645 +/- 0.695 |
| vips | 1.000 +/- 0.000 | 1.745 +/- 0.283 | 3.626 +/- 0.059 | 3.914 +/- 0.630 |

## 6) Final Conclusion for Part 2

- **Parallel scalability is workload-dependent.**
  At 8 threads, speedups range from about **2.95x (canneal)** to **6.28x (radix)**.
- **No single thread count is optimal for all workloads.**
  Some jobs still gain strongly from 8 threads; others saturate earlier and offer weaker incremental benefit.
- **Interference sensitivity and scalability must be combined in scheduling decisions.**
  A good policy should assign more cores to jobs with high parallel return and protect cache-sensitive jobs from heavy cache contention.
- **Actionable strategy for next parts:**
  1) avoid LLC-heavy colocation for highly sensitive jobs;  
  2) use per-workload thread settings (not fixed global value);  
  3) prioritize resources for jobs with higher speedup efficiency.

