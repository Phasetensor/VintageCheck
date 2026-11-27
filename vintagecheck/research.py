"""Reproducible dataset, lineage, validation and reporting operations."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import platform
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import mean, median, variance
from typing import Any

from . import VintageIndex, audit, instant, number
from ._types import Clock, Numeric, Row


def _json_value(value):
    if isinstance(value, datetime):
        return instant(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(number(value))
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def _digest(obj):
    data = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_value,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _typed(value):
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("Dataset object keys must be strings")
        return ["object", [[k, _typed(value[k])] for k in sorted(value)]]
    if isinstance(value, list):
        return ["array", [_typed(v) for v in value]]
    if isinstance(value, datetime):
        return ["instant", instant(value).isoformat()]
    if isinstance(value, Decimal):
        return ["decimal", str(number(value))]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    return [type(value).__name__, value]


def dataset_version(rows: Any, key: str) -> Row:
    """Content-address a JSON dataset using a unique string primary key."""
    fingerprints = {}
    schema: set[tuple[str, str]] = set()
    for row in rows:
        identity = row[key]
        if not isinstance(identity, str) or not identity or identity in fingerprints:
            raise ValueError("Expected distinct nonempty string keys")
        fingerprints[identity] = _digest(_typed(row))
        schema.update((k, type(v).__name__) for k, v in row.items())
    return {
        "format": 2,
        "key": key,
        "schema_sha256": _digest(sorted(schema)),
        "sha256": _digest([key, fingerprints]),
        "rows": fingerprints,
    }


def compare_versions(before: Row, after: Row) -> Row:
    if before["key"] != after["key"]:
        raise ValueError("Incompatible primary keys")
    a, b = before["rows"], after["rows"]
    return {
        "added": sorted(b.keys() - a.keys()),
        "removed": sorted(a.keys() - b.keys()),
        "changed": sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
    }


class Lineage:
    """Inspect declared transformation dependencies and availability including fitting."""

    def __init__(self, nodes: Iterable[Row]) -> None:
        self.nodes: dict[str, Row] = {}
        for row in nodes:
            key = row["id"]
            if key in self.nodes:
                raise ValueError("Duplicate lineage identifier")
            self.nodes[key] = dict(row, parents=tuple(row.get("parents", ())))
        from graphlib import TopologicalSorter

        graph = {k: v["parents"] for k, v in self.nodes.items()}
        self.order = tuple(TopologicalSorter(graph).static_order())

    def trace(self, outputs: Iterable[str]) -> list[str]:
        needed = set(outputs)
        for key in reversed(self.order):
            if key in needed and key in self.nodes:
                needed.update(self.nodes[key]["parents"])
        return sorted(needed)

    def check(self, outputs: Iterable[str], decision_at: Clock) -> Row:
        outputs = tuple(outputs)
        when = instant(decision_at)
        missing, future, conflicts = [], [], []
        trace = self.trace(outputs)
        for key in trace:
            row = self.nodes.get(key)
            if row is None or not row.get("available_at"):
                missing.append(key)
            if row:
                clocks = [
                    row.get(k)
                    for k in (
                        "available_at",
                        "fit_data_end",
                        "window_end",
                        "processed_at",
                        "label_available_at",
                        "deployed_at",
                        "tuning_data_end",
                    )
                ]
                upstream = [
                    self.nodes.get(k, {}).get("available_at") for k in row["parents"]
                ]
                if row.get("available_at") and any(
                    instant(t) > instant(row["available_at"])
                    for t in upstream + clocks[1:]
                    if t
                ):
                    conflicts.append(key)
                if row.get("provenance_complete") is False:
                    missing.append(key)
                if row.get("fitted") and not row.get("fit_data_end"):
                    missing.append(key)
                if any(instant(t) > when for t in clocks if t):
                    future.append(key)
        verdict = (
            "FAIL"
            if future or conflicts
            else "UNKNOWN"
            if missing or not outputs
            else "PASS"
        )
        edges = {
            key: list(self.nodes[key]["parents"]) for key in trace if key in self.nodes
        }
        return {
            "verdict": verdict,
            "future": future,
            "missing": sorted(set(missing)),
            "trace": trace,
            "dependencies": edges,
            "conflicts": conflicts,
        }


def audit_pipeline(nodes: Iterable[Row], decisions: Any) -> list[Row]:
    graph = Lineage(nodes)
    return [
        dict(id=r["id"], **graph.check(r["features"], r["decision_at"]))
        for r in decisions
    ]


def audit_backtest(
    releases: Any,
    decisions: Any,
    threshold: Numeric | None = None,
    nodes: Iterable[Row] = (),
    *,
    sessions: Any = None,
) -> Row:
    """Combine decision vintage and feature evidence, applying FAIL precedence."""
    graph = Lineage(nodes)
    result = audit(releases, decisions, threshold)
    for row in result["decisions"]:
        check = (
            graph.check(row.get("features", ()), row["decision_at"])
            if row.get("decision_at")
            else {
                "verdict": "UNKNOWN",
                "future": [],
                "missing": [],
                "trace": [],
                "dependencies": {},
            }
        )
        row["feature_audit"] = check
        row["vintage_verdict"] = row["verdict"]
        timeline = [
            instant(row[k])
            for k in ("signal_at", "decision_at", "order_at", "execution_at")
            if row.get(k)
        ]
        controls = {"vintage": row["verdict"], "features": check["verdict"]}
        if len(timeline) > 1:
            controls["chronology"] = "PASS" if timeline == sorted(timeline) else "FAIL"
        if sessions is not None:
            session = (
                sessions.status(row["execution_at"])
                if row.get("execution_at")
                else "UNKNOWN"
            )
            controls["session"] = {
                "OPEN": "PASS",
                "CLOSED": "FAIL",
                "UNKNOWN": "UNKNOWN",
            }[session]
        if "universe_audit" in row:
            controls["universe"] = row["universe_audit"]["verdict"]
        if "model" in row:
            controls["model"] = (
                graph.check([row["model"]], row["decision_at"])["verdict"]
                if row.get("decision_at")
                else "UNKNOWN"
            )
        if any(v not in ("PASS", "FAIL", "UNKNOWN") for v in controls.values()):
            raise ValueError("Invalid control verdict")
        row["controls"] = controls
        verdicts = controls.values()
        row["verdict"] = next(v for v in ("FAIL", "UNKNOWN", "PASS") if v in verdicts)
    rows = result["decisions"]
    counts = {
        v: sum(r["verdict"] == v for r in rows) for v in ("PASS", "FAIL", "UNKNOWN")
    }
    result["summary"] = dict(
        counts,
        records=len(rows),
        percent_pass=100 * counts["PASS"] / len(rows) if rows else None,
    )
    result["first_violation_row"] = next(
        (r["decision_row"] for r in rows if r["verdict"] == "FAIL"), None
    )
    return result


def temporal_folds(
    samples: Iterable[Row],
    windows: Iterable[tuple[Clock, Clock]],
    *,
    embargo: timedelta = timedelta(0),
    walk_forward: bool = True,
    train_window: timedelta | None = None,
) -> Iterator[Row]:
    """Purge overlapping closed label intervals; embargo follows each test window."""
    if embargo < timedelta(0):
        raise ValueError("Embargo must be nonnegative")
    samples = list(samples)
    spans = [(instant(r["start"]), instant(r["label_end"])) for r in samples]
    if any(end < start for start, end in spans):
        raise ValueError("Reversed label interval")
    for raw_start, raw_end in windows:
        start, end = instant(raw_start), instant(raw_end)
        if end < start:
            raise ValueError("Reversed validation window")
        test = [i for i, (s, _) in enumerate(spans) if start <= s <= end]
        if not test:
            raise ValueError("Empty validation window")
        horizon = max([end] + [spans[i][1] for i in test])
        train = [
            i
            for i, (s, e) in enumerate(spans)
            if e < start or (not walk_forward and s > horizon + embargo)
        ]
        if train_window is not None:
            if train_window <= timedelta(0):
                raise ValueError("Training window must be positive")
            train = [i for i in train if spans[i][0] >= start - train_window]
        train = [
            i
            for i in train
            if (
                not samples[i].get("feature_available_at")
                or instant(samples[i]["feature_available_at"]) <= spans[i][0]
            )
            and (
                not walk_forward
                or instant(
                    samples[i].get("label_available_at") or samples[i]["label_end"]
                )
                < start
            )
        ]
        if any(
            samples[i].get("feature_available_at")
            and instant(samples[i]["feature_available_at"]) > spans[i][0]
            for i in test
        ):
            raise ValueError("Test feature unavailable at sample time")
        yield {"train": train, "test": test}


def revision_statistics(index: VintageIndex, series: str, period: str) -> Row:
    rows = index.revisions(series, period)
    delta = [number(r["delta"]) for r in rows[1:]]
    ordered = sorted(delta)
    lags = [
        (instant(b["available_at"]) - instant(a["available_at"])).total_seconds()
        for a, b in zip(rows, rows[1:])
    ]
    relative = [
        str(d / number(r["value"])) if number(r["value"]) else None
        for d, r in zip(delta, rows)
    ]

    def quantile(q):
        if not ordered:
            return None
        pos = Decimal(len(ordered) - 1) * q
        i = int(pos)
        return str(
            ordered[i]
            + (pos - i) * (ordered[min(i + 1, len(ordered) - 1)] - ordered[i])
        )

    var = variance(delta) if len(delta) > 1 else None
    return {
        "variance": str(var) if var is not None else None,
        "std": str(var.sqrt()) if var is not None else None,
        "lag_seconds": lags,
        "relative": relative,
        "changes_per_day": 86400 * len(lags) / sum(lags) if sum(lags) else None,
        "quantiles": {q: quantile(Decimal(q)) for q in ("0.25", "0.5", "0.75")},
        "autocorrelation": _corr(delta[:-1], delta[1:]),
        "distance_to_last": [
            str(number(r["value"]) - number(rows[-1]["value"])) for r in rows
        ],
        "count": len(delta),
        "mean": str(mean(delta)) if delta else None,
        "median": str(median(delta)) if delta else None,
        "mean_absolute": str(mean(map(abs, delta))) if delta else None,
        "rms": str((sum((x * x for x in delta), Decimal(0)) / len(delta)).sqrt())
        if delta
        else None,
    }


def _corr(x, y):
    if len(x) < 2:
        return None
    a, b = mean(x), mean(y)
    denom = (sum((n - a) ** 2 for n in x) * sum((n - b) ** 2 for n in y)).sqrt()
    return str(sum((n - a) * (m - b) for n, m in zip(x, y)) / denom) if denom else None


def revision_correlation(
    index: VintageIndex, left: tuple[str, str], right: tuple[str, str]
) -> Row:
    def series(key):
        return {
            instant(r["available_at"]): number(r["delta"])
            for r in index.revisions(*key)
            if r["delta"] is not None
        }

    a, b = series(left), series(right)
    overlap = sorted(a.keys() & b.keys())
    return {
        "pairs": len(overlap),
        "correlation": _corr([a[t] for t in overlap], [b[t] for t in overlap]),
    }


def information_set(
    index: VintageIndex, at: Clock, keys: Iterable[tuple[str, str]]
) -> list[Row]:
    """Preserve explicit missing members of a requested information set."""
    return [
        {"series": s, "period": p, "evidence": index.asof(s, p, at)} for s, p in keys
    ]


def compare_providers(
    left: VintageIndex,
    right: VintageIndex,
    at: Clock,
    keys: Iterable[tuple[str, str]],
    *,
    tolerance: Numeric = 0,
    relative_tolerance: Numeric = 0,
    required_metadata: Iterable[str] = (),
) -> list[Row]:
    tol = number(tolerance)
    rel = number(relative_tolerance)
    if min(tol, rel) < 0:
        raise ValueError("Negative tolerance")
    result = []
    required_metadata = tuple(required_metadata)
    for s, p in keys:
        a, b = left.asof(s, p, at), right.asof(s, p, at)
        diff = number(a["value"]) - number(b["value"]) if a and b else None
        fields = set(required_metadata)
        if a and b:
            fields.update(
                k
                for k in ("currency", "unit", "frequency", "convention", "security_id")
                if k in a or k in b
            )
        mismatches = sorted(
            k
            for k in fields
            if not a or not b or a.get(k) is None or a.get(k) != b.get(k)
        )
        if mismatches:
            diff = None
        verdict = (
            "UNKNOWN"
            if diff is None or a is None or b is None
            else "PASS"
            if abs(diff)
            <= tol + rel * max(abs(number(a["value"])), abs(number(b["value"])))
            else "FAIL"
        )
        result.append(
            {
                "series": s,
                "period": p,
                "left": a,
                "right": b,
                "verdict": verdict,
                "delta": str(diff) if diff is not None else None,
                "incompatible": mismatches,
            }
        )
    return result


def scientific_report(
    audit_result: Row,
    *,
    dataset_versions: Any,
    parameters: Row,
    output: str = "python",
    limitations: Iterable[str] = (),
) -> Row | str:
    """Hash the full JSON evidence, method version, input manifests and parameters."""
    from . import __version__ as software

    controls = [
        v
        for r in audit_result.get("decisions", [])
        for v in r.get("controls", {"vintage": r["verdict"]}).values()
    ]
    payload = {
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
        },
        "limitations": list(limitations),
        "control_counts": {k: controls.count(k) for k in ("PASS", "FAIL", "UNKNOWN")},
        "total_controls": len(controls),
        "total_decisions": len(audit_result.get("decisions", [])),
        "schema": 2,
        "software": "vintagecheck " + software,
        "datasets": dataset_versions,
        "parameters": parameters,
        "result": audit_result,
    }
    payload = json.loads(json.dumps(payload, default=_json_value, allow_nan=False))
    report = dict(payload, report_sha256=_digest(payload))
    if output == "python":
        return report
    serial = json.dumps(report, sort_keys=True, ensure_ascii=False)
    if output == "json":
        return serial
    if output == "html":
        return (
            "<!doctype html><meta charset=utf-8><title>VintageCheck audit</title><pre>"
            + html.escape(serial)
            + "</pre>"
        )
    if output == "csv":
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["field", "json_value"])
        writer.writerows(
            (k, json.dumps(v, sort_keys=True)) for k, v in sorted(report.items())
        )
        return stream.getvalue()
    raise ValueError("Unknown report format")
