# Candidate release checklist

1. Compare the scientific baseline to the pinned 0.3.0 release. Baseline CI on Python 3.12 ran 23 tests: 396/455 statements, 139/182 branches, 19 Ruff diagnostics, formatting changes required and 7 Mypy diagnostics. [Measured baseline](https://github.com/Phasetensor/VintageCheck/actions/runs/46010447955).
2. Require 95% statement coverage and 90% branch coverage independently, passing lint, formatting, type checks, temporal properties, backend parity, case studies and clean distribution installation.
3. Review evidence and limitations. The million-decision benchmark is in the benchmarks/evidence directory.
4. Obtain project owner approval before merging or publishing. The release candidate is not a public PyPI release. Select a fresh final version, update metadata and rerun release checks.
5. Verify the release tag and commit, both GitHub assets and both PyPI hashes against the locally built distributions. Install the public package outside the checkout and run the CLI and research APIs.
