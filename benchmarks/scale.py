"""Deterministic synthetic time and memory experiment; run as a module."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import platform
from statistics import median
from time import perf_counter
import tracemalloc

from vintagecheck import VintageIndex


def experiment(size, pandas=False):
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = [dict(series="S", period="P", available_at=(base + timedelta(minutes=i)).isoformat(),
                 value=str(i)) for i in range(size)]
    queries = [base + timedelta(seconds=60*i-1) for i in range(size)]
    runs = []
    for _ in range(3):
        start = perf_counter()
        index = VintageIndex(reversed(rows))
        indexed = perf_counter()
        found = [index.asof("S", "P", t) for t in queries]
        stop = perf_counter()
        values = [int(r["value"]) if r else -1 for r in found]
        if values != list(range(-1, size-1)):
            raise AssertionError("Closed-form oracle mismatch")
        runs.append((indexed-start, stop-indexed))
    tracemalloc.start()
    measured = VintageIndex(rows)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del measured
    result = dict(size=size, build_seconds=median(x for x,_ in runs),
                  query_seconds=median(y for _,y in runs), index_peak_bytes=peak)
    if pandas:
        import pandas as pd
        left = pd.DataFrame({"time":queries})
        right = pd.DataFrame({"time":pd.to_datetime([r["available_at"] for r in rows]), "v":range(size)})
        for table in (left, right):
            table["time"] = table["time"].astype("datetime64[ns, UTC]")
        start = perf_counter()
        baseline = pd.merge_asof(left, right, on="time")
        result["pandas_join_seconds"] = perf_counter()-start
        if baseline.v.fillna(-1).astype(int).tolist() != values:
            raise AssertionError("pandas.merge_asof mismatch")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pandas", action="store_true")
    args = parser.parse_args()
    print(json.dumps(dict(python=platform.python_version(), platform=platform.platform(),
                          results=[experiment(n, args.pandas) for n in (100,1000,10000)]), indent=2))
