"""Reproduce the full benchmark in isolated worker processes on Linux."""
import argparse
import csv
import hashlib
import json
import platform
import resource
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from vintagecheck import VintageIndex, audit
from vintagecheck.large import parquet_batches

CLOCKS=["2024-01-01T00:00:00Z","2024-01-02T00:00:00Z","2024-01-03T00:00:00Z"]
RELEASES=[dict(series="S",period="P",available_at=t,value=i) for i,t in enumerate(CLOCKS)]
FIELDS=["id","series","period","decision_at","used_available_at"]


def fixture(n, path):
    with (path/"input.csv").open("w",newline="") as file:
        writer=csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        parquet=None
        try:
            for offset in range(0,n,8192):
                rows=[]
                for i in range(offset,min(n,offset+8192)):
                    # Deliberately unordered times and both older and future releases.
                    kind=i%5
                    rows.append(dict(id=str(i),series="S",period="P",decision_at=CLOCKS[(i*7)%3],
                                     used_available_at=None if kind==4 else CLOCKS[(i*11+1)%3]))
                writer.writerows(rows)
                table=pa.Table.from_pylist(rows)
                if parquet is None:
                    parquet=pq.ParquetWriter(path/"input.parquet",table.schema)
                parquet.write_table(table)
        finally:
            if parquet is not None:
                parquet.close()


def batches(path, backend):
    if backend=="python":
        from itertools import islice
        with (path/"input.csv").open() as f:
            reader=csv.DictReader(f)
            while rows:=list(islice(reader,8192)):
                yield rows
    elif backend=="pandas":
        import pandas as pd
        for df in pd.read_csv(path/"input.csv",dtype=str,keep_default_na=False,chunksize=8192):
            yield df.to_dict("records")
    else:
        yield from parquet_batches(path/"input.parquet",backend,8192)


def worker(path, backend):
    start=time.perf_counter()
    index=VintageIndex(RELEASES)
    build=time.perf_counter()-start
    t=time.perf_counter()
    for _ in range(1000):
        index.asof("S","P",CLOCKS[1])
    query=(time.perf_counter()-t)/1000
    t=time.perf_counter()
    for _ in range(1000):
        index.snapshot(CLOCKS[1])
    snapshot=(time.perf_counter()-t)/1000
    counts=dict(tp=0,tn=0,fp=0,fn=0,unknown=0)
    total=correct=0
    audit_time=0.
    arrow_peak=0
    first=None
    for rows in batches(path,backend):
        t=time.perf_counter()
        report=audit(index,rows)
        audit_time+=time.perf_counter()-t
        for r in report["decisions"]:
            i=int(r["id"])
            assert i==total+r["decision_row"]-1
            expected_asof=(i*7)%3
            correct+=r["asof_value"]==str(expected_asof)
            unknown=i%5==4
            target=(i*11+1)%3>expected_asof
            flag=r["verdict"]=="FAIL"
            if unknown:
                assert r["verdict"]=="UNKNOWN"
                counts["unknown"]+=1
            else:
                assert r["verdict"]!="UNKNOWN"
                counts[{(True,True):"tp",(True,False):"fp",(False,True):"fn",(False,False):"tn"}[flag,target]]+=1
            if flag:
                candidate=(r["decision_at"],i+1)
                first=candidate if first is None else min(candidate,first)
        total+=len(rows)
        arrow_peak=max(arrow_peak,pa.default_memory_pool().max_memory())
    elapsed=time.perf_counter()-start
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform!="darwin":
        rss*=1024
    # Separate short profiling pass: tracing every event distorts runtime substantially.
    sample=next(batches(path,backend))
    tracemalloc.start()
    audit(index,sample)
    _,python_peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert counts["fn"]==counts["fp"]==0 and correct==total and first==(CLOCKS[0],1)
    return dict(backend=backend,records=total,build_seconds=build,query_seconds=query,snapshot_seconds=snapshot,
                audit_seconds=audit_time,elapsed_seconds=elapsed,rows_per_second=total/elapsed,peak_rss_bytes=rss,
                arrow_pool_peak_bytes=arrow_peak,python_sample_peak_bytes=python_peak,first_violation=first[1],
                reconstruction_accuracy=correct/total,unknown_rate=counts["unknown"]/total,
                precision=counts["tp"]/(counts["tp"]+counts["fp"]),recall=counts["tp"]/(counts["tp"]+counts["fn"]),**counts)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--directory",type=Path,default=Path("."))
    parser.add_argument("--output",type=Path,default=Path("benchmarks/evidence"))
    parser.add_argument("--backend",choices=["python","pandas","pyarrow","duckdb","polars"],required=True)
    parser.add_argument("--sizes",type=int,nargs="+",default=[1000,10000,100000,1000000])
    args=parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.directory,args.backend)))
        return
    args.output.mkdir(parents=True,exist_ok=True)
    results=[]
    for n in args.sizes:
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            fixture(n,path)
            row=json.loads(subprocess.check_output([sys.executable,"-m","benchmarks.consolidation","--worker","run",
                "--backend",args.backend,"--directory",d]))
            row["input_sha256"]=hashlib.sha256((path/"input.csv").read_bytes()).hexdigest()
            results.append(row)
    with (args.output/(args.backend+".csv")).open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=results[0]);writer.writeheader();writer.writerows(results)
    (args.output/(args.backend+".json")).write_text(json.dumps(dict(results=results,python=sys.version,platform=platform.platform()),indent=2))


if __name__=="__main__":
    main()
