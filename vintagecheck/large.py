"""Stream Parquet decisions through the existing vintage audit."""
from . import audit, VintageIndex


def parquet_batches(path, backend="pyarrow", batch_size=65536):
    if batch_size < 1:
        raise ValueError("Expected a positive batch size")
    if backend == "pyarrow":
        import pyarrow.parquet as pq
        with pq.ParquetFile(path) as file:
            for batch in file.iter_batches(batch_size=batch_size):
                yield batch.to_pylist()
    elif backend == "duckdb":
        import duckdb
        with duckdb.connect() as con:
            cursor = con.execute("SELECT * FROM read_parquet(?)", [str(path)])
            for batch in cursor.fetch_record_batch(batch_size):
                yield batch.to_pylist()
    elif backend == "polars":
        import polars as pl
        for frame in pl.scan_parquet(path).collect_batches(chunk_size=batch_size, maintain_order=True):
            yield frame.to_dicts()
    else:
        raise ValueError("Unknown backend")


def audit_parquet(releases, path, threshold, **options):
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
