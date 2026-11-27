"""Point-in-time audit of explicitly identified data vintages."""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from decimal import Decimal


def instant(value):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("Timestamp requires an explicit UTC offset")
    return timestamp


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Expected a finite number")
    return result


def audit(releases, decisions, threshold):
    """Audit decisions against the supplied release history, ordered by decision time."""
    threshold = number(threshold)
    history = defaultdict(dict)
    for row in releases:
        key = row["series"], row["period"]
        published = instant(row["available_at"])
        if published in history[key]:
            raise ValueError(f"Duplicate release for {key} at {published}")
        history[key][published] = number(row["value"])
    times = {key: sorted(group) for key, group in history.items()}
    results = []
    for index, row in sorted(enumerate(decisions, 1), key=lambda item: instant(item[1]["decision_at"])):
        key = row["series"], row["period"]
        when = instant(row["decision_at"])
        used = instant(row["used_available_at"])
        if key not in history or used not in history[key]:
            raise ValueError(f"Unknown release in decision {index}")
        position = bisect_right(times[key], when)
        latest = times[key][position - 1] if position else None
        value = history[key][used]
        available = history[key][latest] if latest else None
        status = "current"
        if used > when:
            status = "future"
        elif used != latest:
            status = "older_available"
        results.append(dict(row, decision_row=index, status=status, used_value=str(value),
                            asof_available_at=latest.isoformat() if latest else None,
                            asof_value=str(available) if latest else None,
                            used_buy=value > threshold,
                            asof_buy=available > threshold if latest else None))
    first = next((row["decision_row"] for row in results if row["status"] == "future"), None)
    return {"first_future_row": first, "decisions": results}
