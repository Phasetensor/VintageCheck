import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.error import URLError

from vintagecheck import VintageIndex, audit, instant, number
from vintagecheck.__main__ import main
from vintagecheck.io import alfred_observations, fetch_alfred
from vintagecheck.large import parquet_batches
from vintagecheck.market import audit_adjustment, audit_universe, MarketSessions, release_calendar
from vintagecheck.research import (Lineage, audit_backtest, dataset_version, compare_versions,
    compare_providers, revision_statistics, revision_correlation, scientific_report, temporal_folds)

T = [f"2024-01-0{i}T12:00:00Z" for i in range(1,7)]


def quote(i, value, **extra):
    return dict(series="USD",period="Q",value=value,available_at=T[i],**extra)


class ConsolidationTests(unittest.TestCase):
    def test_information_clocks(self):
        r = quote(0, 2, received_at=T[1], processed_at=T[2])
        for policy, n in (("public",0),("operator",2)):
            idx = VintageIndex([r],policy=policy)
            for i,t in enumerate(T):
                self.assertEqual(bool(idx.snapshot(t)), i>=n)
        for rows, policy in [([dict(r,processed_at=T[0])],"operator"),([r],"other"),([dict(r,series="")],"public")]:
            with self.assertRaises(ValueError):
                VintageIndex(rows,policy=policy)
        for value in ("NaN","Infinity"):
            with self.assertRaises(ValueError):
                number(value)

    def test_corporate_ledger(self):
        a=dict(id="x",asset="X",kind="split",ratio=".2",effective_at=T[1],available_at=T[0],revision=1)
        def run(rows, **kw):
            return audit_adjustment(10,50,rows,"X",T[0],T[2],**kw)
        self.assertEqual(run([a])["verdict"],"UNKNOWN")
        self.assertEqual(run([a],complete=False)["verdict"],"UNKNOWN")
        self.assertEqual(run([dict(a,ratio=".3",revision=0),a],complete=True)["verdict"],"PASS")
        for row in [dict(a,ratio=0),dict(a,kind="merger"),dict(a,kind="dividend",cash=20,previous_close=5)]:
            with self.assertRaises(ValueError):
                run([row],complete=True)
        for row in [dict(a,available_at=None),dict(a,kind="dividend"),dict(a,kind="dividend",cash=1,previous_close=10,currency="EUR")]:
            self.assertEqual(run([row],complete=True,currency="USD")["verdict"],"UNKNOWN")
        with self.assertRaises(ValueError):
            run([a,a])
        with self.assertRaises(ValueError):
            run([],tolerance=-1)
        self.assertEqual(run([dict(a,asset="other")],complete=True)["verdict"],"FAIL")

    def test_historical_clocks(self):
        r=dict(asset="A",security_id="ID",start=T[0],announced_at=T[1],end=T[3],end_available_at=T[4])
        self.assertEqual(audit_universe([r],[],T[0],complete=True)["verdict"],"UNKNOWN")
        self.assertEqual(audit_universe([r],["ID"],T[3],complete=True)["knowable"],["ID"])
        with self.assertRaises(ValueError):
            audit_universe([dict(r,end=T[0])],[],T[2])
        event=dict(id="GDP",announced_at=T[0],scheduled_at=T[1],actual_at=T[2],received_at=T[3])
        self.assertNotIn("actual_at",release_calendar([event],T[1])[0])
        with self.assertRaises(ValueError):
            release_calendar([event,event],T[1])
        for sessions,first,last in [([],"2024-02-01","2024-01-01"),([dict(open=T[1],close=T[0])],"2024-01-01","2024-01-02"),([dict(open=T[1],close=T[3])],"2024-01-01","2024-01-02")]:
            with self.assertRaises(ValueError):
                MarketSessions(sessions,"UTC",first,last)

    def test_manifest_types_and_report(self):
        def manifest(v):
            return dataset_version([dict(id="α",v=v)],"id")
        hashes={manifest(v)["sha256"] for v in ("1",Decimal("1"),1.,1,["1"],instant(T[0]),instant(T[0]).date())}
        self.assertEqual(len(hashes),7)
        self.assertEqual(manifest(instant(T[0])),manifest(instant(T[0]).astimezone(timezone(timedelta(hours=3)))))
        with self.assertRaises(ValueError):
            manifest({2:"value"})
        with self.assertRaises(TypeError):
            manifest(set())
        with self.assertRaises(ValueError):
            compare_versions(manifest(1),dict(key="other"))
        with self.assertRaises(ValueError):
            dataset_version([{"id":"x"},{"id":"x"}],"id")
        def report(fmt):
            return scientific_report(dict(decisions=[dict(verdict="UNKNOWN")]),dataset_versions=[],parameters={"x":"<script>","v":Decimal("1"),"stamp":instant(T[0]),"d":instant(T[0]).date()},output=fmt)
        self.assertEqual(json.loads(report("json")),report("python"))
        self.assertNotIn("<script>",report("html"))
        self.assertIn("report_sha256",report("csv"))
        with self.assertRaises(ValueError):
            report("pdf")

    def test_revision_statistics(self):
        rows=[quote(i,n) for i,n in enumerate([0,1,3,7])]
        idx=VintageIndex(rows)
        s=revision_statistics(idx,"USD","Q")
        self.assertAlmostEqual(float(s["variance"]),7/3)
        self.assertEqual(s["quantiles"]["0.5"],"2.0")
        self.assertIsNone(s["relative"][0])
        self.assertEqual(float(s["autocorrelation"]),1)
        self.assertAlmostEqual(float(revision_correlation(idx,("USD","Q"),("USD","Q"))["correlation"]),1)
        self.assertIsNone(revision_statistics(idx,"missing","Q")["mean"])
        flat=VintageIndex([quote(i,1) for i in range(4)])
        self.assertIsNone(revision_statistics(flat,"USD","Q")["autocorrelation"])

    def test_control_evidence(self):
        node=dict(id="scale",parents=["raw"],available_at=T[0],processed_at=T[1],fitted=True,provenance_complete=False)
        nodes=[node,dict(id="raw",available_at=T[1])]
        check=Lineage(nodes).check(["scale"],T[3])
        self.assertEqual(check["conflicts"],["scale"])
        self.assertEqual(check["missing"],["scale"])
        with self.assertRaises(ValueError):
            Lineage(nodes+nodes)
        dec=dict(series="USD",period="Q",used_available_at=T[0],decision_at=T[1],features=["raw"],
                 model="nonexistent",order_at=T[0],execution_at=T[1],universe_audit={"verdict":"PASS"})
        sessions=MarketSessions([],"UTC","2024-01-01","2024-01-06")
        report=audit_backtest([quote(0,1)],[dec,dict(dec,decision_at=None)],nodes=nodes,sessions=sessions)
        self.assertIsNone(report["decisions"][0]["used_buy"])
        self.assertEqual(report["decisions"][0]["controls"]["chronology"],"FAIL")
        self.assertEqual(report["decisions"][0]["controls"]["session"],"FAIL")
        self.assertEqual(report["decisions"][1]["controls"]["model"],"UNKNOWN")
        self.assertIsNone(audit([],[])["summary"]["percent_pass"])

    def test_validation_windows(self):
        samples=[dict(start=T[0],label_end=T[0],label_available_at=T[2]),dict(start=T[2],label_end=T[3])]
        self.assertEqual(next(temporal_folds(samples,[(T[2],T[2])],train_window=timedelta(days=1)))["train"],[])
        cases=[(samples,[(T[1],T[0])],{}),(samples,[(T[5],T[5])],{}),
               (samples,[(T[2],T[2])],{"train_window":timedelta(0)}),
               (samples,[],{"embargo":timedelta(days=-1)}),([dict(start=T[1],label_end=T[0])],[],{}),
               ([dict(samples[0],feature_available_at=T[1])],[(T[0],T[0])],{})]
        for rows,windows,kw in cases:
            with self.assertRaises(ValueError):
                list(temporal_folds(rows,windows,**kw))

    def test_cross_provider_and_batches(self):
        a=VintageIndex([quote(0,100,unit="USD")])
        b=VintageIndex([quote(0,99,unit="USD")])
        keys=[("USD","Q")]
        self.assertEqual(compare_providers(a,b,T[0],keys,relative_tolerance=".01")[0]["verdict"],"PASS")
        self.assertEqual(compare_providers(a,b,T[0],keys,required_metadata=["currency"])[0]["verdict"],"UNKNOWN")
        with self.assertRaises(ValueError):
            compare_providers(a,b,T[0],keys,tolerance=-1)
        for kwargs in [dict(backend="other"),dict(batch_size=0)]:
            with self.assertRaises(ValueError):
                list(parquet_batches("x",**kwargs))

    def test_cli(self):
        for fmt in ("csv","json"):
            with patch("sys.argv",["vintagecheck","examples/releases.csv","examples/decisions.csv","--threshold","1","--format",fmt]),contextlib.redirect_stdout(io.StringIO()) as out:
                main()
            self.assertIn("verdict",out.getvalue())
        with patch("sys.argv",["vintagecheck","missing","missing","--threshold","1"]),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main()

    def test_adapter_invalid_boundaries(self):
        obs=dict(date="2024-01-01",realtime_start="2024-01-01",realtime_end="9999-12-31",value="1")
        clocks={"2024-01-01":T[0]}
        for payload, mapping in [({"count":3,"observations":[obs]},clocks),({"observations":[obs,obs]},clocks),({"observations":[obs]},{"2024-01-01":T[3]})]:
            with self.assertRaises(ValueError):
                alfred_observations("X",json.dumps(payload),mapping)
        def fetch(key):
            return fetch_alfred("X",key,clocks,observation_start="2024-01-01",observation_end="2024-01-02")
        with self.assertRaises(ValueError):
            fetch("")
        with patch("urllib.request.urlopen",side_effect=URLError("offline")),self.assertRaises(OSError):
            fetch("test")
        for page in (dict(count=2,offset=1,observations=[]),dict(count=1,offset=0,observations=[])):
            with patch("urllib.request.urlopen",return_value=io.BytesIO(json.dumps(page).encode())),self.assertRaises(ValueError):
                fetch("test")

    def test_evidence_conflicts(self):
        graph=Lineage([dict(id="pca",available_at=T[1],fit_data_end=T[0],fitted=True,fit_partition="test")])
        self.assertEqual(graph.check(["pca"],T[4])["conflicts"],["pca"])
        with self.assertRaises(ValueError):
            compare_versions({"format":1},{"format":2})
        with self.assertRaises(ValueError):
            release_calendar([dict(id="c",scheduled_at=T[0],announced_at=T[0],actual_at=T[2],received_at=T[1])],T[3])
        r=dict(id="s",asset="X",kind="split",effective_at=T[1],available_at=T[0],ratio=2,revision=1)
        with self.assertRaises(ValueError):
            audit_adjustment(4,2,[r,dict(r,revision=2),r],"X",T[0],T[2],complete=True)

    def test_stable_identifier_ticker_and_merger(self):
        m=dict(asset="OLD",security_id="stable",start=T[1],announced_at=T[0],end=T[4],
               exit_reason="acquired",ticker_history=[dict(ticker="OLD",start=T[1]),dict(ticker="NEW",start=T[3])])
        for pos,ticker in [(1,"OLD"),(3,"NEW")]:
            result=audit_universe([m],["stable"],T[pos],complete=True,current=[])
            self.assertEqual(result["tickers"]["stable"],ticker)
            self.assertEqual(result["knowable"],["stable"])
        self.assertEqual(audit_universe([m],[],T[5])["exits"],{"stable":"acquired"})
        self.assertEqual(audit_universe([m],[],T[0])["effective"],[])
