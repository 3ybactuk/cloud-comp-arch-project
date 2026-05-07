# Part 2 conclusions (auto-generated)

## Part 2a: Interference sensitivity
- Metric: slowdown = runtime_with_interference / runtime_baseline.
- Each point uses 3 runs (mean +/- sample std).

Average slowdown across all 7 benchmarks by interference:
- ibench-llc: 1.824x
- ibench-l1i: 1.596x
- ibench-l1d: 1.512x
- ibench-l2: 1.476x
- ibench-membw: 1.427x
- ibench-cpu: 1.391x
- baseline: 1.000x

Worst observed benchmark/interference pair: barnes + ibench-llc = 2.495x slowdown.

Interpretation: cache-related interference dominates. Overall impact ranking is: LLC > L1I > L1D > L2 > MemBW > CPU.

## Part 2b: Parallel scaling
- Metric: speedup(Tn) = T1 / Tn.
- Each point uses 3 runs (mean +/- sample std).

Speedup at 8 threads:
- radix: 6.28x
- barnes: 5.05x
- freqmine: 4.95x
- streamcluster: 4.64x
- vips: 3.91x
- blackscholes: 3.06x
- canneal: 2.95x

Best scaling at 8 threads: radix (6.28x).
Weakest scaling at 8 threads: canneal (2.95x).

Interpretation: workloads scale differently; parallelism policy should be workload-aware, not one-size-fits-all.
