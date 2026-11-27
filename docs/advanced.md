# Research audit API (0.3)

All timestamp inputs require explicit UTC offsets. Examples below use short aliases
M = vintagecheck.market, R = vintagecheck.research and L = vintagecheck.large.
Install the optional financial schedule and Parquet backends with
`pip install 'vintagecheck[tables,large,calendars]'` on Python 3.12.

| Capability | Public interface | Evidence and interpretation |
|---|---|---|
| Historical universes | M.audit_universe(memberships, holdings, at, complete=False) | Each asset has start and optional exclusive end. Compare the claimed entire universe, including assets later delisted. An omitted known member fails. Set complete=True only for a complete reference roster. Unresolved extra assets otherwise produce UNKNOWN. |
| Corporate actions | M.audit_adjustment(raw_close, adjusted_close, actions, asset, price_at, asof) | Supply a complete single-currency action ledger with unique IDs, availability and effective clocks. For a split, ratio is new shares / old shares. For a cash dividend, cash and previous_close must be on the same share basis. The result reproduces backward price adjustment under this convention; special distributions require separate modeling. |
| Feature leakage | R.Lineage(nodes).check(outputs, decision_at) | Check declared availability, window_end and fit_data_end across every ancestor. Future data dominates missing evidence. |
| Backtest audit | R.audit_backtest(releases, decisions, threshold, nodes) | Add a features list to each decision. Returns both evidence paths, combined verdicts, and earliest confirmed violation. An undeclared feature set yields UNKNOWN unless there is a confirmed vintage violation. |
| Temporal validation | R.temporal_folds(samples, windows, embargo=..., walk_forward=True) | Inputs carry start and label_end. Test windows are closed. Exclude labels reaching the test start. With future training enabled, exclude through the last test label plus embargo. Walk-forward admits only past training. Returns original zero-based sample positions. |
| Dataset versioning | R.dataset_version(rows, key), R.compare_versions(before, after) | Persist JSON manifests with a unique string primary key. Row order is irrelevant. Compare additions, removals and content changes. The manifest fingerprints evidence; retain source datasets separately to reconstruct past versions. |
| Data lineage | R.Lineage(nodes).trace(outputs) | Nodes have id and parents. Cycles are invalid; absent dependencies remain visible as missing evidence. Attach transformation metadata to the dataset manifest to version a pipeline definition. |
| Economic calendar | M.release_calendar(events, asof) | Supply versioned event schedules with id, announced_at, scheduled_at and optional boolean cancelled. Reconstruct the schedule known at the cutoff. Calendar entries do not establish feed receipt. |
| Market sessions | M.MarketSessions.from_exchange(exchange, first_date, last_date).status(when) | Exchange calendars handle local holidays, DST, early closes and breaks. Supply your own dated open/close intervals for bespoke venues. Sessions are half-open. Outside coverage yields UNKNOWN. |
| Revision statistics | R.revision_statistics(index, series, period) | Consecutive arithmetic deltas in source units. Report count, signed mean, median, mean absolute and RMS revision. A single vintage yields zero revisions and null statistics. |
| Information sets | R.information_set(index, at, keys) | Return the eligible evidence for each requested series-period pair, retaining unavailable members explicitly. |
| Cross-provider validation | R.compare_providers(left, right, at, keys, tolerance=0) | Compare aligned identifiers, units and conventions at a common cutoff. Absolute tolerance in those units. Missing evidence yields UNKNOWN; a mismatch indicates disagreement, not which provider is correct. |
| Large datasets | L.parquet_batches(path, backend, batch_size), L.audit_parquet(releases, path, threshold, ...) | Stream decisions using pyarrow, duckdb or polars. Reports are sorted within batches and use global input row IDs. Release history is held in memory. To find the global earliest violation, compare decision timestamps across batches. |
| ML pipeline audit | R.audit_pipeline(nodes, decisions) | Each prediction supplies id, features and decision_at. Mark learned nodes fitted=True and provide fit_data_end. Include labels as dependencies for supervised fits. The auditor evaluates declared metadata; it cannot infer unrecorded transformations from model weights. |
| Scientific reporting | R.scientific_report(result, dataset_versions=..., parameters=...) | Return a JSON report plus SHA-256 over the schema, package version, complete result, parameters and manifests. Supply JSON-native evidence values. Hashes identify content and can be independently recomputed; they do not authenticate the original source. |

## Example

From a source checkout run `python -m examples.research_audit`. It prints the
reproducible report for a decision whose economic vintage is eligible while the
normalizer's fit contains future data. The output includes manifests for releases,
decisions and the pipeline definition.

For the consolidation candidate, see [module evidence](traceability.md) and [methods](methodology.md).
Corporate-action callers must explicitly attest completeness with complete=True to obtain a definitive result.
Dataset manifests now carry format=2. Supply original data to compare versions across fingerprint formats.
