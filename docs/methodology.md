# Methods and scope

A vintage is identified by series, observation period and offset-aware publication time.
Calendar periods are opaque strings. Temporal order uses absolute instants. Effective
availability is the later of publication and optional feed receipt. A decision can
use releases effective at or before its instant. When delayed vintages arrive out of
order, the newest published eligible vintage prevails. An older eligible vintage
passes availability validation and carries the status older_available.

VintageIndex.asof returns one eligible release. snapshot reconstructs each distinct
series-period pair. revisions returns publication-ordered changes in original units
and the previous evidence digest. The digest covers normalized record content with
keys sorted using Python JSON encoding. It identifies the supplied evidence. It
does not authenticate the provider. Provide source identifiers and receipt times
where available. Availability is conditional on the completeness of supplied history.

Each decision produces PASS (identified and eligible), FAIL (identified but future)
or UNKNOWN (missing decision time or unidentified vintage). Malformed clocks, duplicate
vintage keys and nonfinite release values are schema errors. The report counts each
class and divides passes by all decisions. An empty denominator yields null. Decisions
are sorted chronologically with untimed rows last. The threshold comparison is strict;
signal_changed is null when either side cannot be evaluated.

## ALFRED

Use alfred_observations(series, payload, release_times) from vintagecheck.io with a
complete FRED series/observations JSON response (output_type=1, full real-time
history). Provide an independently verified local timestamp with UTC offset for
each real-time start date. A date alone does not establish intraday availability.
The adapter handles contiguous intervals with an open-ended last vintage; it
rejects gaps, missing values, truncated coverage and unmerged pages. Downloading
the observations and obtaining source timestamps are separate input preparation steps.
See the [FRED API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html).

## Validation and scaling

The GDP example checks an actual revision against two synthetic decisions. Unit tests
also exercise equal-time eligibility, late receipt, unverifiable decisions, source
lineage and cross-format equivalence. From the checkout run
`python -m benchmarks.scale --pandas` with the tables extra installed. The workload
has one series-period pair, reversed input, one query per vintage one second before
publication, and an exact integer oracle. It compares all answers against pandas
merge_asof. Build/query values are medians of three trials. Peak traced Python
allocations are measured separately during index construction. pandas timings cover
only the pre-sorted join; index construction also validates and hashes evidence.
Timings depend on the machine. See benchmarks/results.json for one recorded run.
Index construction takes O(R log R) worst-case time and O(R) space; one lookup costs
O(log R) plus the size of the returned record. Snapshot cost grows with the number
of distinct series-period pairs. The benchmark does not estimate trading performance.

The optional fetch_alfred function requests all historical real-time periods for a
bounded observation window using a caller-supplied FRED API key. It checks page
offsets and counts before normalizing. The test suite exercises pagination with
recorded-format synthetic responses. A seeded panel experiment independently scans
all releases in six series-period groups for each of 40 decision times, including
delayed and out-of-order receipts, and compares every snapshot to that oracle.

## Candidate temporal policies and denominators

public uses publication time; operator uses the latest supplied publication, receipt and processing clock. Inconsistent clocks are rejected. The cumulative set of eligible evidence is nested as time increases under fixed metadata. A latest-value snapshot is a projection of that set and its selected values may change. Corrections to historical metadata can invalidate prior reconstructions. Missing receipt times use the remaining declared clocks; this is not proof of actual delivery.

Without a threshold the API omits signal inference (null signals). A decision may supply signal_at, order_at, execution_at, model and universe_audit. Chronology and session eligibility are separate controls. Report totals count decisions separately from individual tests. The earliest violation is ordered by decision time with source position breaking ties.

The dataset manifest's format 2 hashes recursively tagged JSON-compatible values. Map keys are sorted, array order matters, datetimes normalize to UTC, and Decimal retains its textual precision. Strings, timestamps, integers, decimals and floats are distinct. A separate fingerprint summarizes observed top-level column names and runtime types. Schema inference on empty input cannot recover a declared external schema. Report hashes include Python version and OS family; changing these can change the digest.

Sample revision variance uses n-1. Quantiles interpolate ordered revisions at (n-1)p. Relative revisions have the previous level as denominator; zero yields null. Cross-series correlation pairs only matching instants. No IID bootstrap or news/noise decomposition is inferred from sparse dependent vintages. Splits use new/old shares, dividend factors require matching currency and share basis. Arithmetic uses the caller's Decimal context and tolerance in price units. Historical schedules refer to published continuous trading hours; auction products may differ. See [HKEX](https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market) and [JPX](https://www.jpx.co.jp/english/systems/equities-trading/).
