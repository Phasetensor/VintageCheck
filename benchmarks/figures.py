"""Render diagnostics from recorded runs and explicit deterministic fixtures."""
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from vintagecheck import VintageIndex
from vintagecheck.research import (Lineage, compare_providers, revision_statistics, temporal_folds,
                                  information_set)
from vintagecheck.io import read_rows

DEST=Path("benchmarks/evidence")
plt.rcParams.update({"svg.hashsalt":"vintagecheck","axes.spines.top":False,"axes.spines.right":False,
                     "axes.prop_cycle":plt.cycler(color=["#154875","#bc552d","#568b77","#82549c","#727b86"])})
series=["python","pandas","pyarrow","duckdb","polars"]


def save(fig,name):
    fig.tight_layout()
    fig.savefig(DEST/(name+".svg"),metadata={"Date":None})
    plt.close(fig)


def run():
    raw=[json.loads((DEST/(name+".json")).read_text()) for name in series]
    all_rows=[r for run in raw for r in run["results"]]
    with (DEST/"scalability.csv").open("w",newline="") as f:
        csv_out=csv.DictWriter(f,all_rows[0].keys());csv_out.writeheader();csv_out.writerows(all_rows)
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for backend,run in zip(series,raw):
        rows=run["results"]
        axes[0].loglog([r["records"] for r in rows],[r["elapsed_seconds"] for r in rows],"o-",label=backend)
        axes[1].semilogx([r["records"] for r in rows],[r["peak_rss_bytes"]/2**20 for r in rows],"o-")
        axes[2].bar(backend,rows[-1]["rows_per_second"]/1000)
    for ax,title,unit in zip(axes,["End-to-end runtime","Peak process RSS","One million decisions"],["Seconds","MiB","Thousands of decisions/s"]):
        ax.set_title(title);ax.set_ylabel(unit)
    for ax in axes[:2]:ax.set_xlabel("Decisions")
    axes[0].legend(fontsize=7);axes[2].tick_params(axis="x",rotation=35)
    save(fig,"scaling")
    cases=json.loads((DEST/"cases.json").read_text())
    obs=read_rows("examples/releases.csv")
    index=VintageIndex(obs)
    key=(obs[0]["series"],obs[0]["period"])
    stats=revision_statistics(index,*key)
    fig,axes=plt.subplots(2,3,figsize=(12,6.5))
    axes[0,0].hist([float(r["delta"]) for r in index.revisions(*key)[1:]],bins=3)
    axes[0,0].set_title("BEA revision (one observation)");axes[0,0].set_xlabel("Percentage points")
    axes[0,1].bar(["GDP revision"],[x/86400 for x in stats["lag_seconds"]]);axes[0,1].set_title("Publication lag (days)")
    axes[0,2].plot([1,2],list(map(float,stats["distance_to_last"])),"o-");axes[0,2].set_title("Distance to last supplied vintage");axes[0,2].set_xlabel("Vintage")
    universe=cases["universe"];axes[1,0].bar(["Historical","Survivors"],[universe["historical_mean"],universe["current_mean"]]);axes[1,0].set_title("Synthetic mean return")
    ml=cases["ml"]["results"];axes[1,1].bar([v["verdict"] for v in ml.values()],[v["mse"] for v in ml.values()]);axes[1,1].set_title("Scaler leakage (synthetic MSE)")
    dates=["2024-04-01T00:00:00Z"]+[r["available_at"] for r in obs]
    vals=[information_set(index,d,[key])[0]["evidence"] for d in dates]
    axes[1,2].plot(range(3),[float(r["value"]) if r else float("nan") for r in vals],"o-");axes[1,2].set_title("Point-in-time GDP (%)");axes[1,2].set_xticks(range(3),["Before","First","Revised"])
    save(fig,"scientific")
    fig,axes=plt.subplots(1,3,figsize=(12,3.5))
    times=[f"2024-01-0{i}T00:00:00Z" for i in range(1,8)]
    folds=list(temporal_folds([dict(start=t,label_end=t) for t in times],[(times[i],times[i+1]) for i in (2,4)]))
    for n,fold in enumerate(folds):
        for label in ("train","test"):
            axes[0].scatter(fold[label],[n]*len(fold[label]),label=label if n==0 else None,color={"train":"#154875","test":"#bc552d"}[label])
    axes[0].legend();axes[0].set_title("Walk-forward folds");axes[0].set_xlabel("Sample index")
    graph=Lineage([dict(id="quote",available_at=times[0]),dict(id="scaler",parents=["quote"],available_at=times[1]),dict(id="model",parents=["scaler"],available_at=times[2])])
    deps=graph.check(["model"],times[3])["dependencies"];nodes=["quote","scaler","model"]
    axes[1].imshow([[int(x in deps[y]) for x in nodes] for y in nodes],cmap="Blues",vmin=0,vmax=1)
    axes[1].set_xticks(range(3),nodes);axes[1].set_yticks(range(3),nodes);axes[1].set_title("Declared parent matrix")
    alt=VintageIndex([dict(obs[0],value="1.4"),dict(obs[1],value="1.3")])
    diffs=[compare_providers(index,alt,d,[key])[0]["delta"] for d in dates[1:]]
    axes[2].bar(["First","Revised"],list(map(float,diffs)));axes[2].set_title("Synthetic provider differences (pp)")
    save(fig,"validation")
if __name__=="__main__":
    run()
