import importlib.util
import tempfile
import unittest
from pathlib import Path

from vintagecheck import VintageIndex, audit
from vintagecheck.io import alfred_observations, read_rows


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = read_rows("examples/releases.csv")
        self.decisions = read_rows("examples/decisions.csv")
        self.index = VintageIndex(self.rows)

    def test_three_verdicts_and_impact(self):
        missing = dict(self.decisions[0], used_available_at=None)
        report = audit(self.index, self.decisions + [missing], 1.5)
        self.assertEqual(report["summary"]["records"], 3)
        self.assertEqual([report["summary"][k] for k in ("PASS","FAIL","UNKNOWN")], [1,1,1])
        self.assertTrue(report["decisions"][0]["signal_changed"])
        self.assertEqual(report["first_future_row"], 1)

    def test_late_old_vintage_does_not_supersede_revision(self):
        self.rows[0]["received_at"] = "2024-06-01T00:00:00Z"
        index = VintageIndex(self.rows)
        self.assertEqual(index.snapshot("2024-05-01T00:00:00Z"), [])
        self.assertEqual(index.snapshot("2024-07-01T00:00:00Z")[0]["value"], "1.3")
        report = audit(index, [dict(self.decisions[0], used_available_at=self.rows[0]["available_at"])], 1)
        self.assertEqual(report["decisions"][0]["verdict"], "FAIL")

    def test_revision_evidence(self):
        first, second = self.index.revisions(self.rows[0]["series"], "2024Q1")
        self.assertEqual(second["delta"], "-0.3")
        self.assertEqual(second["previous_sha256"], first["record_sha256"])
        first["value"] = "0"
        self.assertEqual(self.index.lookup(first["series"], first["period"], first["available_at"])["value"], "1.6")

    def test_alfred_contract(self):
        row = dict(date="2024-01-01", realtime_start="2024-04-25", realtime_end="9999-12-31", value="1.6")
        clocks = {"2024-04-25":self.rows[0]["available_at"]}
        def adapt(**updates):
            return alfred_observations("GDP", {"observations":[dict(row, **updates)]}, clocks)
        self.assertEqual(VintageIndex(adapt()).snapshot("2024-04-26T00:00:00Z")[0]["source"], "ALFRED/GDP")
        for updates in [dict(value="."), dict(realtime_end="2024-05-01"), dict(realtime_start="2024-04-24")]:
            with self.assertRaises(ValueError):
                adapt(**updates)

    @unittest.skipUnless(importlib.util.find_spec("pyarrow") and importlib.util.find_spec("pandas"), "tables extra")
    def test_dataframe_and_parquet(self):
        import pandas as pd
        frame = pd.DataFrame(self.decisions + [dict(self.decisions[0], used_available_at=pd.NA)])
        expected = audit(self.rows, frame, 1.5)
        self.assertEqual(expected["summary"]["UNKNOWN"], 1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "decisions.parquet"
            frame.to_parquet(path, index=False)
            self.assertEqual(audit(pd.DataFrame(self.rows), read_rows(path), 1.5), expected)

    def test_paginated_alfred_revision(self):
        import io
        import json
        from unittest.mock import patch
        from vintagecheck.io import fetch_alfred
        starts = ["2024-04-25", "2024-05-30"]
        ends = ["2024-05-29", "9999-12-31"]
        pages = []
        for i, row in enumerate(self.rows):
            obs = dict(date="2024-01-01", realtime_start=starts[i], realtime_end=ends[i], value=row["value"])
            pages.append(io.BytesIO(json.dumps(dict(offset=i, count=2, observations=[obs])).encode()))
        with patch("urllib.request.urlopen", side_effect=pages) as fetch:
            rows = fetch_alfred("GDP", "test", dict(zip(starts, [r["available_at"] for r in self.rows])),
                                observation_start="2024-01-01", observation_end="2024-03-31")
        self.assertIn("offset=1", fetch.call_args.args[0])
        self.assertEqual(VintageIndex(rows).revisions("GDP", "2024-01-01")[1]["delta"], "-0.3")

    def test_randomized_panel_against_exhaustive_reference(self):
        from datetime import datetime, timedelta, timezone
        import random
        rng = random.Random(47)
        base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        rows = []
        for series in ["A", "B", "C"]:
            for period in ["P", "Q"]:
                for minute in range(25):
                    pub = base + timedelta(minutes=minute)
                    rows.append(dict(series=series, period=period, available_at=pub.isoformat(),
                                     received_at=(pub+timedelta(minutes=rng.randrange(12))).isoformat(),
                                     value=str(rng.randrange(100))))
        rng.shuffle(rows)
        index = VintageIndex(rows)
        for minute in range(40):
            when = (base + timedelta(minutes=minute)).isoformat()
            expected = {}
            for row in rows:
                if max(row["available_at"], row["received_at"]) <= when:
                    key = row["series"], row["period"]
                    expected[key] = max(expected.get(key, ""), row["available_at"])
            actual = {(r["series"], r["period"]): r["available_at"] for r in index.snapshot(when)}
            self.assertEqual(actual, expected)
