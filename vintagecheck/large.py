"""Stream Parquet decisions through the existing vintage audit."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from . import VintageIndex, audit
from ._types import Numeric, Row


def parquet_batches(
    path: str | Path, backend: str = "pyarrow", batch_size: int = 65536
) -> Iterator[list[Row]]:
    if batch_size < 1:
        raise ValueError("Expected a positive batch size")
    if backend not in ("pyarrow", "polars", "duckdb"):
        raise ValueError("Unknown backend")
    import pyarrow as pa
    import pyarrow.parquet as pq

    for field in pq.read_schema(path):
        if pa.types.is_timestamp(field.type) and field.type.unit == "ns":
            raise ValueError("Normalize nanosecond timestamps before auditing")
    if backend == "pyarrow":
        with pq.ParquetFile(path) as file:
            for batch in file.iter_batches(batch_size=batch_size):
                yield batch.to_pylist()
    elif backend == "duckdb":
        import duckdb

        with duckdb.connect() as con:
            cursor = con.execute("SELECT * FROM read_parquet(?)", [str(path)])
            columns = [field[0] for field in cursor.description]
            while batch := cursor.fetchmany(batch_size):
                yield [dict(zip(columns, row)) for row in batch]
    else:
        import polars as pl

        for frame in pl.scan_parquet(path).collect_batches(
            chunk_size=batch_size, maintain_order=True
        ):
            yield frame.to_dicts()


def audit_parquet(
    releases: Any, path: str | Path, threshold: Numeric | None, **options: Any
) -> Iterator[Row]:
    """Yield batch reports with global source row numbers; releases reside in memory."""
    index = releases if isinstance(releases, VintageIndex) else VintageIndex(releases)
    offset = 0
    for batch in parquet_batches(path, **options):
        report = audit(index, batch, threshold)
        for row in report["decisions"]:
            row["decision_row"] += offset
        if report["first_future_row"] is not None:
            report["first_future_row"] += offset
        yield report
        offset += len(batch)


def summarize_batches(reports: Iterable[Row]) -> Row:
    """Merge batch counts and earliest chronological violation without retaining decisions."""
    from . import instant

    counts = {"PASS": 0, "FAIL": 0, "UNKNOWN": 0}
    first = None
    for report in reports:
        for verdict in counts:
            counts[verdict] += report["summary"][verdict]
        for row in report["decisions"]:
            if row["verdict"] == "FAIL":
                candidate = instant(row["decision_at"]), row["decision_row"]
                first = candidate if first is None else min(candidate, first)
    return dict(
        counts,
        records=sum(counts.values()),
        first_future_row=first[1] if first else None,
    )
