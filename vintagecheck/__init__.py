"""Point-in-time reconstruction and temporal evidence auditing."""
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json


def instant(value):
    timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("Timestamp requires an explicit UTC offset")
    return timestamp


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Expected a finite number")
    return result


def records(rows):
    if hasattr(rows, "to_dict") and hasattr(rows, "columns"):
        return rows.astype(object).where(rows.notna(), None).to_dict(orient="records")
    return rows


class VintageIndex:
    """Immutable-by-convention release history indexed by series and period."""

    def __init__(self, releases):
        self._groups = defaultdict(dict)
        for raw in records(releases):
            row = dict(raw)
            key = row["series"], row["period"]
            if not all(isinstance(part, str) and part.strip() for part in key):
                raise ValueError("Release series and period must be nonempty strings")
            stamp = instant(row["available_at"])
            received = row.get("received_at")
            effective = max(stamp, instant(received)) if received else stamp
            if stamp in self._groups[key]:
                raise ValueError(f"Duplicate release for {key} at {stamp}")
            row.update(value=str(number(row["value"])), available_at=stamp.isoformat(),
                       effective_at=effective.isoformat())
            evidence = json.dumps(row, default=str, sort_keys=True, allow_nan=False)
            row["record_sha256"] = sha256(evidence.encode()).hexdigest()
            self._groups[key][stamp] = row
        self._times = {}
        self._winners = {}
        for key, group in self._groups.items():
            events = sorted((instant(row["effective_at"]), stamp) for stamp, row in group.items())
            times, winners = [], []
            latest = None
            for arrival, stamp in events:
                latest = stamp if latest is None else max(latest, stamp)
                times.append(arrival)
                winners.append(latest)
            self._times[key], self._winners[key] = times, winners

    def lookup(self, series, period, available_at):
        row = self._groups.get((series, period), {}).get(instant(available_at))
        return dict(row) if row else None

    def asof(self, series, period, decision_at):
        key = series, period
        pos = bisect_right(self._times.get(key, ()), instant(decision_at))
        return dict(self._groups[key][self._winners[key][pos - 1]]) if pos else None

    def snapshot(self, decision_at):
        rows = (self.asof(*key, decision_at) for key in sorted(self._groups))
        return [row for row in rows if row is not None]

    def revisions(self, series, period):
        previous = None
        changes = []
        for stamp, row in sorted(self._groups.get((series, period), {}).items()):
            delta = None if previous is None else str(number(row["value"]) - number(previous["value"]))
            changes.append(dict(row, delta=delta, previous_sha256=previous["record_sha256"] if previous else None))
            previous = row
        return changes


def audit(releases, decisions, threshold):
    """PASS means accessible under the supplied clock and history."""
    threshold = number(threshold)
    index = releases if isinstance(releases, VintageIndex) else VintageIndex(releases)
    ordered = []
    for n, raw in enumerate(records(decisions), 1):
        row = dict(raw)
        # A malformed clock is a schema error. An absent clock is missing evidence.
        when = instant(row["decision_at"]) if row.get("decision_at") else None
        ordered.append((when, n, row))
    ordered.sort(key=lambda item: (item[0] is None, item[0]))
    results = []
    for when, n, row in ordered:
        key = row.get("series"), row.get("period")
        used = index.lookup(*key, row["used_available_at"]) if row.get("used_available_at") else None
        latest = index.asof(*key, when) if when else None
        if when is None:
            verdict, status = "UNKNOWN", "missing_decision_time"
        elif used is None:
            verdict, status = "UNKNOWN", "unidentified_release"
        elif instant(used["effective_at"]) > when:
            verdict, status = "FAIL", "future"
        elif latest and latest["record_sha256"] == used["record_sha256"]:
            verdict, status = "PASS", "current"
        else:
            verdict, status = "PASS", "older_available"
        used_buy = number(used["value"]) > threshold if used else None
        asof_buy = number(latest["value"]) > threshold if latest else None
        results.append(dict(row, decision_row=n, status=status, verdict=verdict,
                            used_value=used["value"] if used else None,
                            asof_available_at=latest["available_at"] if latest else None,
                            asof_value=latest["value"] if latest else None, used_buy=used_buy,
                            asof_buy=asof_buy, used_evidence=used, asof_evidence=latest,
                            signal_changed=(used_buy != asof_buy) if None not in (used_buy, asof_buy) else None))
    counts = Counter(row["verdict"] for row in results)
    summary = {label: counts[label] for label in ("PASS", "FAIL", "UNKNOWN")}
    summary.update(records=len(results), percent_pass=100 * counts["PASS"] / len(results) if results else None)
    first = next((r["decision_row"] for r in results if r["verdict"] == "FAIL"), None)
    return {"first_future_row": first, "decisions": results, "summary": summary}
