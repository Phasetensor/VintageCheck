# VintageCheck

Audit the vintage used by a backtest against the release history available at each decision. Supply one release table and one decision table with the columns in examples/. Each decision identifies the vintage it actually used by series, period and used_available_at. Values alone cannot identify a vintage. Supply a complete history for the period being audited and explicit UTC offsets.

From the project root, run:

    python3 -m vintagecheck examples/releases.csv examples/decisions.csv --threshold 1.5
    python3 -m unittest discover -s tests -v

Python API: audit(releases, decisions, threshold) in vintagecheck accepts iterables of mappings. The rule buys when value > threshold. Times equal to the decision time are eligible. Use the actual feed availability time if ingestion latency matters. The report distinguishes future, current and older_available releases and gives the earliest future use as a one-based decision record number. Missing historical observations produce null signals.

The offline example uses annualized US real GDP growth for 2024 Q1 from the BEA [advance estimate](https://www.bea.gov/news/2024/gross-domestic-product-first-quarter-2024-advance-estimate) and [second estimate](https://www.bea.gov/news/2024/gross-domestic-product-first-quarter-2024-second-estimate-and-corporate-profits). Both releases were issued at 08:30 EDT. Decisions and threshold are synthetic. On May 1 the available 1.6% triggered a buy while the later 1.3% did not. On May 31 the second estimate was available.

## Additional interfaces

Install with `pip install "vintagecheck[tables]"` for DataFrame and Parquet support.
Pass DataFrames directly to audit or pass .parquet paths to the CLI. Use --format csv
for a decision table. JSON includes verdict totals and evidence for each decision.
Use VintageIndex(releases).snapshot(decision_at) to reconstruct a panel, and
VintageIndex(releases).revisions(series, period) to inspect revision deltas.
See [methodology](docs/methodology.md) for availability, provenance, ALFRED input
requirements and reproducible benchmarks.

## Research audits

Version 0.3 adds historical universe and price-adjustment audits, feature lineage, temporal validation, exchange calendars, reproducible reports and streaming Parquet backends. See the [15-module API guide](docs/advanced.md) and [worked audit](examples/research_audit.py).

## Scientific consolidation candidate

The branch includes the [fifteen-module evidence matrix](docs/traceability.md), [reproducible benchmarks and case studies](benchmarks/README.md) and [release conditions](docs/release.md). Existing published versions are listed on PyPI.
