import unittest
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec

from vintagecheck import VintageIndex
from vintagecheck.large import summarize_batches
from vintagecheck.market import MarketSessions
from vintagecheck.research import temporal_folds


class Oracles(unittest.TestCase):
    def test_cross_batch_minimum(self):
        at="2024-01-01T00:00:00Z"
        reports=[dict(summary=dict(PASS=0,FAIL=1,UNKNOWN=0),decisions=[dict(verdict="FAIL",decision_at=s,decision_row=i)])
                 for i,s in [(1,"2024-01-03T00:00:00Z"),(5,at),(7,at)]]
        self.assertEqual(summarize_batches(reports)["first_future_row"],5)
        self.assertIsNone(summarize_batches([])["first_future_row"])

    @unittest.skipUnless(find_spec("exchange_calendars"),"calendar integration")
    def test_venues(self):
        for venue,clock,expected in [("XLON","2024-07-03T09:00:00Z","OPEN"),
                                     ("XHKG","2024-07-03T04:30:00Z","CLOSED"),
                                     ("XTKS","2024-07-03T03:00:00Z","CLOSED")]:
            calendar=MarketSessions.from_exchange(venue,"2024-07-02","2024-07-05")
            self.assertEqual(calendar.status(clock),expected)

    @unittest.skipUnless(find_spec("hypothesis"),"property suite")
    def test_snapshot_and_folds(self):
        from hypothesis import given, strategies as st, settings
        base=datetime(2024,1,1,tzinfo=timezone.utc)
        def t(i):
            return base+timedelta(hours=i)
        @settings(max_examples=100,derandomize=True,deadline=None)
        @given(st.lists(st.integers(0,8),min_size=5,max_size=30),st.integers(0,4))
        def check(lags,cut):
            rows=[dict(series="X",period="P",value=i,available_at=t(i),received_at=t(i+lag)) for i,lag in enumerate(lags)]
            idx=VintageIndex(reversed(rows))
            expected=max((i for i,lag in enumerate(lags) if i+lag<=cut), default=None)
            found=idx.asof("X","P",t(cut))
            self.assertEqual(int(found["value"]) if found else None,expected)
            self.assertEqual(idx.snapshot(t(cut)),VintageIndex(rows[:cut+1]).snapshot(t(cut)))
            samples=[dict(start=t(i),label_end=t(i+lag)) for i,lag in enumerate(lags)]
            fold=next(temporal_folds(samples,[(t(cut),t(cut))],walk_forward=False,embargo=timedelta(hours=1)))
            allowed=[i for i,lag in enumerate(lags) if set(range(i,i+lag+1)).isdisjoint(range(cut,cut+lags[cut]+2))]
            self.assertEqual(fold["train"],allowed)
        check()

    @unittest.skipUnless(all(find_spec(k) for k in ("duckdb","polars","pyarrow")), "Parquet integration")
    def test_timestamp_decimal_nulls_and_batch_order(self):
        from decimal import Decimal
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import pyarrow as pa
        import pyarrow.parquet as pq
        from vintagecheck.large import audit_parquet, parquet_batches
        base=datetime(2024,1,1,tzinfo=timezone.utc)
        clocks=[base+timedelta(microseconds=i) for i in (2,1,0)]
        with TemporaryDirectory() as folder:
            p=Path(folder)/"data.parquet"
            pq.write_table(pa.table(dict(series=["X"]*3,period=["P"]*3, used_available_at=[clocks[0], None, clocks[0]],
                                         decision_at=clocks,weight=[Decimal("0.05")]*3)),p)
            for backend in ("pyarrow","duckdb","polars"):
                reports=list(audit_parquet([dict(series="X",period="P",available_at=clocks[0],value=1)],p,0,
                                           backend=backend,batch_size=1))
                self.assertEqual(summarize_batches(reports),dict(PASS=1,FAIL=1,UNKNOWN=1,records=3,first_future_row=3))
                self.assertEqual(reports[0]["decisions"][0]["weight"], Decimal(".05"))
            pq.write_table(pa.table({"clock":pa.array([1],type=pa.timestamp("ns",tz="UTC"))}),p)
            for backend in ("pyarrow","duckdb","polars"):
                with self.assertRaises(ValueError):
                    list(parquet_batches(p,backend))

    @unittest.skipUnless(find_spec("numpy"),"independent statistics")
    def test_numpy_revision_oracle(self):
        import numpy as np
        from vintagecheck.research import revision_statistics
        vals=[1,4,-1,15,2,9]
        idx=VintageIndex([dict(series="X",period="Q",available_at=f"2024-02-0{i+1}T00:00:00Z",value=x) for i,x in enumerate(vals)])
        stats=revision_statistics(idx,"X","Q")
        diff=np.diff(vals)
        for key,expected in [("mean",np.mean(diff)),("variance",np.var(diff,ddof=1)),("std",np.std(diff,ddof=1)),
                             ("rms",np.sqrt(np.mean(diff**2))),("autocorrelation",np.corrcoef(diff[:-1],diff[1:])[0,1])]:
            self.assertAlmostEqual(float(stats[key]),float(expected))
        for q,val in stats["quantiles"].items():
            self.assertAlmostEqual(float(val),float(np.quantile(diff,float(q))))
