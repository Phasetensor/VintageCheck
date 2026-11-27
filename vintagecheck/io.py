"""File and ALFRED observations adapters."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import instant, number
from ._types import Clock, Row


def read_rows(path: str | Path) -> list[Row]:
    path = Path(path)
    if path.suffix.lower() in (".parquet", ".pq"):
        import pyarrow.parquet as pq

        return pq.read_table(path).to_pylist()
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def alfred_observations(
    series: str, payload: Any, release_times: Mapping[str, Clock]
) -> list[Row]:
    """Import a complete output_type=1 observations response with explicit release clocks.

    release_times maps realtime_start dates to verified offset-aware timestamps.
    Missing-value periods and finite validity gaps cannot be represented by a
    carry-forward index. Reject them instead of silently carrying stale values.
    """
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    rows = payload["observations"]
    if int(payload.get("offset", 0)) or int(payload.get("count", len(rows))) != len(
        rows
    ):
        raise ValueError("Incomplete ALFRED response: combine all pages first")
    intervals: dict[str, list[tuple[date, date]]] = {}
    result = []
    for item in rows:
        start = date.fromisoformat(item["realtime_start"])
        end = date.fromisoformat(item["realtime_end"])
        period = date.fromisoformat(item["date"]).isoformat()
        if end < start or item["value"] == ".":
            raise ValueError("Invalid or missing ALFRED interval")
        intervals.setdefault(period, []).append((start, end))
        stamp = release_times.get(start.isoformat())
        if stamp is None:
            raise ValueError(f"Missing verified release time for {start}")
        clock = instant(stamp)
        if datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).date() != start:
            raise ValueError(
                "Provide the timestamp in the release date's local timezone"
            )
        result.append(
            {
                "series": series,
                "period": period,
                "value": str(number(item["value"])),
                "available_at": clock.isoformat(),
                "realtime_start": str(start),
                "realtime_end": str(end),
                "source": "ALFRED/" + series,
            }
        )
    for spans in intervals.values():
        spans.sort()
        if spans[-1][1] != date.max:
            raise ValueError(
                "Final interval must be open-ended for carry-forward semantics"
            )
        for (_, end), (start, _) in zip(spans, spans[1:]):
            if start.toordinal() != end.toordinal() + 1:
                raise ValueError(
                    "Overlapping or discontinuous ALFRED vintage intervals"
                )
    return result


def fetch_alfred(
    series: str,
    api_key: str,
    release_times: Mapping[str, Clock],
    *,
    observation_start: str,
    observation_end: str,
) -> list[Row]:
    """Fetch all real-time periods for an explicitly bounded observation window."""
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import urlopen

    start = date.fromisoformat(observation_start)
    end = date.fromisoformat(observation_end)
    if start > end or not api_key:
        raise ValueError("Expected an API key and an ordered observation window")
    params = {
        "series_id": series,
        "api_key": api_key,
        "file_type": "json",
        "output_type": 1,
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "limit": 100000,
        "offset": 0,
    }
    rows: list[Row] = []
    expected = None
    while True:
        url = "https://api.stlouisfed.org/fred/series/observations?" + urlencode(params)
        try:
            with urlopen(url, timeout=30) as response:
                page = json.load(response)
        except (HTTPError, URLError) as error:
            raise OSError(
                "ALFRED request failed (HTTP status {})".format(
                    getattr(error, "code", "unknown")
                )
            ) from None
        count = int(page["count"])
        if int(page["offset"]) != len(rows) or (
            expected is not None and count != expected
        ):
            raise ValueError("Inconsistent ALFRED pagination")
        expected = count
        rows.extend(page["observations"])
        if len(rows) == expected:
            break
        if not page["observations"] or len(rows) > expected:
            raise ValueError("Incomplete ALFRED pagination")
        params["offset"] = len(rows)
    return alfred_observations(series, {"observations": rows}, release_times)
