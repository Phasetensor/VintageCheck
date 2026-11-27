"""Historical universes, corporate actions, releases and trading sessions."""
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import instant, number


def audit_universe(memberships, holdings, at, *, complete=False):
    """Compare an entire claimed universe with half-open membership intervals."""
    when = instant(at)
    effective, expected, hidden = set(), set(), set()
    for row in memberships:
        start = instant(row["start"])
        end = instant(row["end"]) if row.get("end") else None
        if end is not None and end <= start:
            raise ValueError("Membership end must follow start")
        asset = row.get("security_id") or row["asset"]
        if start <= when and (end is None or when < end):
            effective.add(asset)
        known = max((instant(row[k]) for k in ("announced_at","available_at") if row.get(k)), default=start)
        removal = instant(row["end_available_at"]) if row.get("end_available_at") else end
        if start <= when and known <= when and (end is None or end > when or removal > when):
            expected.add(asset)
        elif asset in effective and known > when:
            hidden.add(asset)
    actual = set(holdings)
    omitted = sorted(expected - actual)
    extra = sorted(actual - expected)
    verdict = "UNKNOWN"
    if omitted or (complete and extra):
        verdict = "FAIL"
    elif complete and not hidden:
        verdict = "PASS"
    return dict(verdict=verdict, omitted=omitted, unmatched=extra, effective=sorted(effective), knowable=sorted(expected), declared=sorted(actual))


def audit_adjustment(raw_close, adjusted_close, actions, asset, price_at, asof, *, tolerance="0.000001", complete=None, currency=None, share_basis=None):
    """Validate backward price adjustment in a supplied complete single-currency ledger."""
    start, cutoff = instant(price_at), instant(asof)
    if cutoff < start or number(tolerance) < 0:
        raise ValueError("Invalid adjustment window or tolerance")
    def unknown(reason):
        return dict(expected=None, action_ids=[], verdict="UNKNOWN", reason=reason)
    if complete is False:
        return unknown("incomplete_ledger")
    selected = {}
    for row in actions:
        if row["asset"] != asset:
            continue
        if not row.get("available_at"):
            return unknown("missing_availability")
        known = max(instant(row.get("announced_at") or row["available_at"]), instant(row["available_at"]))
        if known > cutoff:
            continue
        event = row["id"]
        if event in selected:
            older = selected[event]
            if "revision" not in row or "revision" not in older or row["revision"] == older["revision"]:
                raise ValueError("Ambiguous action revision")
            if row["revision"] < older["revision"]:
                continue
        selected[event] = row
    actions = sorted(selected.values(),key=lambda r:(instant(r["effective_at"]),r["id"]))
    factor = Decimal(1)
    used, seen = [], set()
    for row in actions:
        if row["asset"] != asset:
            continue
        effective = instant(row["effective_at"])
        if not start < effective <= cutoff or instant(row["available_at"]) > cutoff:
            continue
        if row["id"] in seen:
            raise ValueError("Duplicate action identifier")
        seen.add(row["id"])
        if row["kind"] == "split":
            ratio = number(row["ratio"])
            if ratio <= 0:
                raise ValueError("Split ratio must be positive")
            factor /= ratio
        elif row["kind"] == "dividend":
            if row.get("previous_close") is None or row.get("cash") is None:
                return unknown("missing_reference")
            if (currency and row.get("currency") != currency) or (share_basis and row.get("share_basis") != share_basis):
                return unknown("incompatible_reference")
            previous, cash = number(row["previous_close"]), number(row["cash"])
            if not 0 <= cash < previous:
                raise ValueError("Dividend must be smaller than the positive reference close")
            factor *= (previous - cash) / previous
        else:
            raise ValueError("Unsupported corporate action")
        used.append(row["id"])
    expected = number(raw_close) * factor
    return dict(expected=str(expected), action_ids=sorted(used),
                verdict=("PASS" if complete is True else "UNKNOWN") if abs(expected-number(adjusted_close)) <= number(tolerance) else ("FAIL" if complete is True else "UNKNOWN"))


def release_calendar(events, asof):
    """Latest known schedule per event, including cancellations and rescheduling."""
    when = instant(asof)
    revisions = {}
    seen = set()
    for row in events:
        event, known = row["id"], instant(row["announced_at"])
        instant(row["scheduled_at"])
        if (event, known) in seen:
            raise ValueError("Ambiguous schedule revision")
        seen.add((event, known))
        if known <= when and (event not in revisions or known > revisions[event][0]):
            visible = dict(row)
            for field in ("actual_at", "received_at", "ingested_at"):
                if visible.get(field) and instant(visible[field]) > when:
                    visible.pop(field)
            revisions[event] = known, visible
    active = [r for _, r in revisions.values() if not r.get("cancelled", False)]
    return sorted(active, key=lambda r: (instant(r["scheduled_at"]), r["id"]))


class MarketSessions:
    """Explicit dated sessions. Gaps inside certified coverage represent closures."""
    def __init__(self, sessions, timezone, first_date, last_date):
        self.zone = ZoneInfo(timezone)
        self.first, self.last = date.fromisoformat(first_date), date.fromisoformat(last_date)
        if self.first > self.last:
            raise ValueError("Reversed calendar coverage")
        self.sessions = sorted((instant(r["open"]), instant(r["close"])) for r in sessions)
        previous = None
        for start, end in self.sessions:
            if end <= start or (previous and start < previous):
                raise ValueError("Invalid or overlapping sessions")
            if start.astimezone(self.zone).date() < self.first or (end-timedelta(microseconds=1)).astimezone(self.zone).date() > self.last:
                raise ValueError("Session outside calendar coverage")
            previous = end

        self._opens = [start for start, _ in self.sessions]

    @classmethod
    def from_exchange(cls, exchange, first_date, last_date):
        """Load exchange sessions, holidays, early closes and intraday breaks."""
        import exchange_calendars as xc
        import pandas as pd
        first, last = date.fromisoformat(first_date), date.fromisoformat(last_date)
        calendar = xc.get_calendar(exchange, start=str(first-timedelta(days=10)),
                                   end=str(last+timedelta(days=10)))
        zone = ZoneInfo(str(calendar.tz))
        from datetime import datetime, time
        lower = datetime.combine(first, time.min, zone)
        upper = datetime.combine(last+timedelta(days=1), time.min, zone)
        intervals = []
        for row in calendar.schedule.to_dict("records"):
            segments = [(row["open"], row["close"])]
            if pd.notna(row["break_start"]):
                segments = [(row["open"], row["break_start"]), (row["break_end"], row["close"])]
            for start, end in segments:
                if start < upper and end > lower:
                    intervals.append(dict(open=max(start, lower), close=min(end, upper)))
        return cls(intervals, str(calendar.tz), first_date, last_date)

    def status(self, when):
        stamp = instant(when)
        day = stamp.astimezone(self.zone).date()
        if not self.first <= day <= self.last:
            return "UNKNOWN"
        pos = bisect_right(self._opens, stamp)
        return "OPEN" if pos and stamp < self.sessions[pos-1][1] else "CLOSED"
