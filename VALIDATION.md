# Scientific release candidate 0.4.0rc1

The implementation tested at 9ca4a654dfe2683d6b69e55380c2bd353837f2c5 is on scientific-consolidation. All eleven jobs passed in [run 46012623371](https://github.com/Phasetensor/VintageCheck/actions/runs/46012623371). The public release remains 0.3.0. Final owner approval, a final-version build, tag, Trusted Publishing and verification of public bytes are pending.

| Check | Measured result |
|---|---|
| Full test suite, Python 3.12 with all extras | 40 passed, including deterministic Hypothesis cases |
| Core compatibility | 3.9, 3.11, 3.12, 3.13 and 3.14 |
| Statement coverage | 647/652 = 99.23% |
| Branch coverage | 282/288 = 97.92% |
| Ruff lint and formatting | Passed for the package |
| Mypy | Passed on seven package source files |
| Distributions | Wheel and sdist built; strict Twine checks; clean wheel installation; CLI and research example executed outside checkout |
| SHA-256 of this run's candidate wheel | 9e883bd84531b87b506313a5838cfce673ea5f41654169c9c3b35c452d9cce16 |
| SHA-256 of this run's candidate sdist | 92764e4fef6133ab1fdb99faadd7ef11cf6500413259454ccfb7316e6cb1cae5 |

## Million-decision experiment

| Input path | End-to-end seconds | Peak RSS, MiB |
|---|---:|---:|
| Native Python CSV | 5.83 | 186.7 |
| Pandas CSV | 10.62 | 197.5 |
| Arrow Parquet | 8.83 | 182.4 |
| DuckDB Parquet | 9.87 | 219.0 |
| Polars Parquet | 9.25 | 210.7 |

Each path agreed with the synthetic oracle on every record: 266667 true positives, 533333 true negatives, zero false positives or negatives and 200000 unknowns. Precision and recall among classifiable decisions were 1. Reconstruction accuracy was 1 and the unknown rate was 0.2. This single-run workload holds the three-row release index constant. Peak RSS includes shared interpreter and native allocations. Measurements across separate hosted runners are not hardware-controlled competitive benchmarks.

## Scientific results

- BEA macro case: the historical 1.6% rate triggered the synthetic 1.5% rule; the later 1.3% rate suppressed it.
- Synthetic survivorship: one omitted asset changed mean return from -5% to +10%, a 15 percentage-point selection effect.
- Synthetic ML: fixed split, Ridge model, target and features. Training-only scaling had MSE 2.5523400 and PASS. Full-sample scaling had MSE 2.2929125 and FAIL.
- CurveMetric 1.5.0 was installed from its published distribution. Actual Treasury bank-discount observations, under an explicit hypothetical receipt policy, produced a 26-week PV of 97.38627778 per 100 face. Future quotes produced 97.39133333. A parallel +1 bp zero-rate shift changed eligible PV by -0.00485585, matching the independent discount-factor oracle.

All fifteen APIs have scoped tests. Independent validation is specific to the cases in [the matrix](docs/traceability.md). The benchmark records [timing and memory](benchmarks/evidence/scalability.csv); [case evidence](benchmarks/evidence/cases.json) records the quote IDs, provenance and results. The optional ten-million workload and live authenticated ALFRED ingestion were not exercised. Vendor receipt times, complete market universes, trading profits, liquidity, and authenticity of third-party input are outside the evidence established by this run.
