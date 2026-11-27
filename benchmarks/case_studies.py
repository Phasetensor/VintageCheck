"""Real BEA observations and explicitly synthetic universe, ML and quote fixtures."""
from importlib.metadata import version
import json
from pathlib import Path
from math import exp, log
from datetime import date, timedelta
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from curvemetric import Curve, compare, present_value
from vintagecheck import VintageIndex, audit
from vintagecheck.io import read_rows
from vintagecheck.market import audit_universe
from vintagecheck.research import audit_pipeline, information_set, dataset_version


def treasury_curve():
    # Rates are historical observations. Publication and receipt clocks below
    # are scenario assumptions; the source supplies a date, not an arrival log.
    source="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?field_tdr_date_value_month=202405&type=daily_treasury_bill_rates"
    quotes=[]
    for row in read_rows("examples/treasury_quotes.csv"):
        day=date.fromisoformat(row["date"])+timedelta(days=1)
        quotes.append(dict(series="TBILL",period=row["weeks"],value=row["bank_discount_percent"],
                           source=source,observation_date=row["date"],unit="percent",currency="USD",
                           convention="bank_discount_ACT360",available_at=f"{day}T00:00:00Z",
                           received_at=f"{day}T00:05:00Z",id=row["date"]+"-"+row["weeks"]))
    index=VintageIndex(quotes)
    keys=[("TBILL",str(weeks)) for weeks in (4,13,26)]
    years=[7*int(weeks)/365 for _,weeks in keys]
    results=[]
    times=["2024-05-02T12:00:00Z","2024-05-03T12:00:00Z"]
    curves=[]
    for at in times:
        rows=[r["evidence"] for r in information_set(index,at,keys)]
        dfs=[1-float(r["value"])/100*int(r["period"])*7/360 for r in rows]
        rates=[-log(df)/year for df,year in zip(dfs,years)]
        curve=Curve.from_zero_rates(years,rates)
        curves.append(curve)
        pv=float(present_value(curve,[years[-1]],[100]))
        assert abs(pv-100*dfs[-1])<1e-10
        results.append(dict(ids=[r["id"] for r in rows],pv=pv))
    assert all(x.startswith("2024-05-01") for x in results[0]["ids"])
    return dict(source=source,clock_policy="synthetic next UTC day plus five-minute receipt",valuation=times[0],
                eligible=results[0],contaminated=results[1],zero_bp=compare(*curves,years)["metrics"]["zero_bp"],
                manifest=dataset_version(quotes,"id"))


def run():
    assert version("curvemetric")=="1.5.0"
    releases=read_rows("examples/releases.csv")
    decisions=read_rows("examples/decisions.csv")
    macro=audit(releases,decisions,1.5)
    first=macro["decisions"][0]
    assert first["verdict"]=="FAIL" and first["signal_changed"]
    t0, t1, t2 = [f"2024-01-0{i}T12:00:00Z" for i in (1,2,3)]
    members=[dict(asset="A",start=t0,end=t2),dict(asset="B",start=t0)]
    selection=audit_universe(members,["B"],t1,complete=True)
    returns={"A":-.2,"B":.1}
    historic=np.mean([returns[k] for k in selection["knowable"]])
    current=returns["B"]
    assert selection["omitted"]==["A"]
    x=np.arange(20,dtype=float).reshape(-1,1)
    y=np.sin(x[:,0]/4)
    ml={}
    for name, fit, end in [("valid",x[:12],t0),("contaminated",x,t2)]:
        scaler=StandardScaler().fit(fit)
        model=Ridge(alpha=3).fit(scaler.transform(x[:12]),y[:12])
        pred=model.predict(scaler.transform(x[12:]))
        check=audit_pipeline([dict(id="scale",available_at=end,fitted=True,fit_data_end=end)],
                             [dict(id="test",features=["scale"],decision_at=t1)])[0]
        ml[name]=dict(mse=float(np.mean((pred-y[12:])**2)),verdict=check["verdict"])
    assert [ml[k]["verdict"] for k in ("valid","contaminated")]==["PASS","FAIL"]
    tenors=[1,3,5]
    quotes=[dict(series="USD",period=str(t),value=str(.02+t*.001),available_at=t0,received_at=t0,
                 currency="USD",convention="continuous_zero",unit="annual_fraction",source="synthetic")
            for t in tenors]
    quotes.append(dict(quotes[-1],available_at=t2,received_at=t2,value=".04"))
    idx=VintageIndex(quotes)
    keys=[("USD",str(t)) for t in tenors]
    def curve(at):
        selected=[x["evidence"] for x in information_set(idx,at,keys)]
        return Curve.from_zero_rates(tenors,[float(x["value"]) for x in selected])
    valid, dirty=curve(t1),curve(t2)
    pvs=[float(present_value(c,[5],[100])) for c in (valid,dirty)]
    assert abs(pvs[0]-100*exp(-5*.025))<1e-9 and abs(pvs[1]-100*exp(-5*.04))<1e-9
    return dict(historical_curve=treasury_curve(),macro=dict(data="BEA real; decisions synthetic",report=macro),
                universe=dict(data="synthetic",audit=selection,historical_mean=historic,current_mean=current),
                ml=dict(data="synthetic",train=12,test=8,results=ml),
                curve=dict(data="synthetic",provider="curvemetric "+version("curvemetric"),eligible_pv=pvs[0],
                           contaminated_pv=pvs[1],diagnostics=compare(valid,dirty,tenors)["metrics"]["zero_bp"],
                           manifest=dataset_version([dict(q,id=str(i)) for i,q in enumerate(quotes)],"id")))


if __name__=="__main__":
    target=Path("benchmarks/evidence");target.mkdir(exist_ok=True)
    (target/"cases.json").write_text(json.dumps(run(),indent=2))
