import unittest

from vintagecheck import audit
from vintagecheck.__main__ import read_rows


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.releases = read_rows("examples/releases.csv")
        self.decisions = read_rows("examples/decisions.csv")

    def run_at(self, timestamp):
        row = dict(self.decisions[0], decision_at=timestamp)
        return audit(self.releases, [row], "1.5")["decisions"][0]

    def test_real_revision(self):
        report = audit(self.releases, reversed(self.decisions), "1.5")
        self.assertEqual(report["first_future_row"], 2)
        first, second = report["decisions"]
        self.assertEqual((first["used_buy"], first["asof_buy"]), (False, True))
        self.assertEqual(second["status"], "current")

    def test_release_boundary(self):
        self.assertEqual(self.run_at("2024-05-30T12:29:59Z")["status"], "future")
        self.assertEqual(self.run_at("2024-05-30T12:30:00Z")["status"], "current")

    def test_before_first_release(self):
        self.assertIsNone(self.run_at("2024-04-01T00:00:00Z")["asof_buy"])

    def test_older_known_release(self):
        self.decisions[1]["used_available_at"] = self.releases[0]["available_at"]
        self.assertEqual(audit(self.releases, self.decisions, 1)["decisions"][1]["status"], "older_available")

    def test_duplicate(self):
        with self.assertRaises(ValueError):
            audit(self.releases * 2, [], 1)

    def test_naive_time(self):
        with self.assertRaises(ValueError):
            self.run_at("2024-05-30")
