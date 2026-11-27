from datetime import timedelta
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from vintagecheck import VintageIndex
from vintagecheck.large import audit_parquet
from vintagecheck.market import audit_universe, audit_adjustment, release_calendar, MarketSessions
from vintagecheck.research import (
    Lineage, audit_pipeline, audit_backtest, temporal_folds, dataset_version,
    compare_versions, revision_statistics, information_set, compare_providers, scientific_report,
)


def t(day):
    return f"2024-01-{day:02}T12:00:00Z"


def release(i, value):
    return dict(series="GDP", period="Q1", available_at=t(i), value=value)


class FinancialHistoryTests(unittest.TestCase):
    def test_universe_includes_delisted(self):
        rows = [dict(asset="A", start=t(1), end=t(3)), dict(asset="B", start=t(2))]
        self.assertEqual(audit_universe(rows, ["B"], t(2))["omitted"], ["A"])
        self.assertEqual(audit_universe(rows, ["B"], t(3), complete=True)["verdict"], "PASS")
        self.assertEqual(audit_universe(rows, ["C","B"], t(3))["verdict"], "UNKNOWN")

    def test_corporate_actions(self):
        common = dict(asset="A", available_at=t(1), effective_at=t(2))
        actions = [dict(common, id="S",kind="split",ratio=2),
                   dict(common,id="D",kind="dividend",cash=2,previous_close=100)]
        self.assertEqual(audit_adjustment(100,49,actions,"A",t(1),t(2))["verdict"],"PASS")
        self.assertEqual(audit_adjustment(100,49,actions,"A",t(1),t(1))["verdict"],"FAIL")
        actions[0]["available_at"] = t(4)
        self.assertEqual(audit_adjustment(100,98,actions,"A",t(1),t(2))["action_ids"],["D"])

    def test_release_calendar(self):
        a = dict(id="CPI", announced_at=t(1), scheduled_at=t(4))
        b = dict(a, announced_at=t(2), scheduled_at=t(5))
        c = dict(b, announced_at=t(3), cancelled=True)
        self.assertEqual(release_calendar([c,b,a],t(1)),[a])
        self.assertEqual(release_calendar([c,b,a],t(2)),[b])
        self.assertEqual(release_calendar([c,b,a],t(3)),[])

    def test_sessions(self):
        market = MarketSessions([{"open":"2024-03-11T09:30:00-04:00","close":"2024-03-11T16:00:00-04:00"}],
                                "America/New_York","2024-03-09","2024-03-11")
        self.assertEqual(market.status("2024-03-11T13:30:00Z"),"OPEN")
        self.assertEqual(market.status("2024-03-11T20:00:00Z"),"CLOSED")
        self.assertEqual(market.status("2024-03-10T18:00:00Z"),"CLOSED")
        self.assertEqual(market.status(t(1)),"UNKNOWN")

    @unittest.skipUnless(find_spec("exchange_calendars"), "calendars extra")
    def test_exchange_holiday_and_early_close(self):
        m = MarketSessions.from_exchange("XNYS","2024-07-01","2024-07-05")
        self.assertEqual(m.status("2024-07-03T16:59:00Z"),"OPEN")
        self.assertEqual(m.status("2024-07-03T17:00:00Z"),"CLOSED")
        self.assertEqual(m.status("2024-07-04T15:00:00Z"),"CLOSED")

    def test_revision_statistics_information_set_and_provider_evidence(self):
        a = VintageIndex([release(1,2),release(3,4),release(4,3)])
        b = VintageIndex([release(2,3)])
        self.assertEqual(revision_statistics(a,"GDP","Q1")["mean_absolute"],"1.5")
        keys = [("GDP","Q1"),("GDP","Q2")]
        self.assertIsNone(information_set(a,t(2),keys)[1]["evidence"])
        self.assertEqual([r["verdict"] for r in compare_providers(a,b,t(3),keys)],["FAIL","UNKNOWN"])
        self.assertEqual(compare_providers(a,b,t(3),keys,tolerance=1)[0]["verdict"],"PASS")

    def test_fold_purging_and_embargo(self):
        samples = [dict(start=t(s),label_end=t(e)) for s,e in [(1,1),(2,3),(3,5),(4,4),(6,6),(7,8)]]
        def fold(forward):
            return next(temporal_folds(samples,[(t(3),t(4))],embargo=timedelta(days=1),walk_forward=forward))
        self.assertEqual(fold(True),dict(train=[0],test=[2,3]))
        self.assertEqual(fold(False),dict(train=[0,5],test=[2,3]))

    def test_dataset_and_report_reproducibility(self):
        rows = [dict(id="x",v=1),dict(id="y",v=2)]
        a = dataset_version(rows,"id")
        self.assertEqual(a,dataset_version(reversed(rows),"id"))
        b = dataset_version([dict(id="z",v=1),dict(id="y",v=3)],"id")
        self.assertEqual(compare_versions(a,b),dict(added=["z"],removed=["x"],changed=["y"]))
        def report(x):
            return scientific_report({"summary":{"FAIL":1}},dataset_versions=[x],parameters={"threshold":1})
        self.assertEqual(report(a),report(a))
        self.assertNotEqual(report(a)["report_sha256"],report(b)["report_sha256"])

    def test_feature_lineage_pipeline_and_backtest(self):
        nodes = [dict(id="raw", available_at=t(1)), dict(id="scale",parents=["raw"],available_at=t(2),
                                                         fitted=True, fit_data_end=t(5))]
        graph = Lineage(nodes)
        self.assertEqual(graph.trace(["scale"]),["raw","scale"])
        self.assertEqual(graph.check(["missing"],t(2))["verdict"],"UNKNOWN")
        with self.assertRaises(ValueError):
            Lineage([dict(id="a",parents=["a"])])
        decision = dict(id="d", series="GDP",period="Q1",decision_at=t(3),used_available_at=t(1),features=["scale"])
        self.assertEqual(audit_pipeline(nodes,[decision])[0]["future"],["scale"])
        result = audit_backtest([release(1,2)],[decision],1,nodes)
        self.assertEqual(result["summary"]["FAIL"],1)
        self.assertEqual(result["decisions"][0]["vintage_verdict"],"PASS")
        self.assertEqual(result["first_violation_row"],1)

    @unittest.skipUnless(all(find_spec(n) for n in ("polars","duckdb","pyarrow")), "large extra")
    def test_three_parquet_backends(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with TemporaryDirectory() as directory:
            path = Path(directory)/"decisions.parquet"
            rows = [dict(series="GDP",period="Q1",decision_at=t(day),used_available_at=t(2)) for day in (1,2,3,4,5)]
            pq.write_table(pa.Table.from_pylist(rows),path,row_group_size=2)
            for backend in ("pyarrow","duckdb","polars"):
                reports = list(audit_parquet([release(2,1)],path,2,backend=backend,batch_size=2))
                flat = [r for report in reports for r in report["decisions"]]
                self.assertEqual([r["decision_row"] for r in flat],list(range(1,6)))
                self.assertEqual([r["verdict"] for r in flat],["FAIL"]+["PASS"]*4)
