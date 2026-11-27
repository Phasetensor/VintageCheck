"""Reproducible dataset, lineage, validation and reporting operations."""
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import mean, median

from . import audit, instant, number


def _json_value(value):
    if isinstance(value, datetime):
        return instant(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(number(value))
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def _digest(obj):
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=_json_value)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def dataset_version(rows, key):
    """Content-address a JSON dataset using a unique string primary key."""
    fingerprints = {}
    for row in rows:
        identity = row[key]
        if not isinstance(identity, str) or not identity or identity in fingerprints:
            raise ValueError("Expected distinct nonempty string keys")
        fingerprints[identity] = _digest(row)
    return dict(key=key, sha256=_digest([key, fingerprints]), rows=fingerprints)


def compare_versions(before, after):
    if before["key"] != after["key"]:
        raise ValueError("Incompatible primary keys")
    a, b = before["rows"], after["rows"]
    return dict(added=sorted(b.keys()-a.keys()), removed=sorted(a.keys()-b.keys()),
                changed=sorted(k for k in a.keys() & b.keys() if a[k] != b[k]))


class Lineage:
    """Inspect declared transformation dependencies and availability including fitting."""
    def __init__(self, nodes):
        self.nodes = {}
        for row in nodes:
            key = row["id"]
            if key in self.nodes:
                raise ValueError("Duplicate lineage identifier")
            self.nodes[key] = dict(row, parents=tuple(row.get("parents", ())))
        from graphlib import TopologicalSorter
        graph = {k: v["parents"] for k, v in self.nodes.items()}
        self.order = tuple(TopologicalSorter(graph).static_order())

    def trace(self, outputs):
        needed = set(outputs)
        for key in reversed(self.order):
            if key in needed and key in self.nodes:
                needed.update(self.nodes[key]["parents"])
        return sorted(needed)

    def check(self, outputs, decision_at):
        outputs = tuple(outputs)
        when = instant(decision_at)
        missing, future = [], []
        trace = self.trace(outputs)
        for key in trace:
            row = self.nodes.get(key)
            if row is None or not row.get("available_at"):
                missing.append(key)
            if row:
                clocks = [row.get("available_at"), row.get("fit_data_end"), row.get("window_end")]
                if row.get("fitted") and not row.get("fit_data_end"):
                    missing.append(key)
                if any(instant(t) > when for t in clocks if t):
                    future.append(key)
        verdict = "FAIL" if future else "UNKNOWN" if missing or not outputs else "PASS"
        edges = {key: list(self.nodes[key]["parents"]) for key in trace if key in self.nodes}
        return dict(verdict=verdict, future=future, missing=sorted(set(missing)), trace=trace,
                    dependencies=edges)


def audit_pipeline(nodes, decisions):
    graph = Lineage(nodes)
    return [dict(id=r["id"], **graph.check(r["features"], r["decision_at"])) for r in decisions]


def audit_backtest(releases, decisions, threshold, nodes):
    """Combine decision vintage and feature evidence, applying FAIL precedence."""
    graph = Lineage(nodes)
    result = audit(releases, decisions, threshold)
    for row in result["decisions"]:
        check = (graph.check(row.get("features", ()), row["decision_at"]) if row.get("decision_at")
                 else dict(verdict="UNKNOWN", future=[], missing=[], trace=[], dependencies={}))
        row["feature_audit"] = check
        row["vintage_verdict"] = row["verdict"]
        verdicts = (check["verdict"], row["verdict"])
        row["verdict"] = next(v for v in ("FAIL","UNKNOWN","PASS") if v in verdicts)
    rows = result["decisions"]
    counts = {v: sum(r["verdict"] == v for r in rows) for v in ("PASS","FAIL","UNKNOWN")}
    result["summary"] = dict(counts, records=len(rows), percent_pass=100*counts["PASS"]/len(rows) if rows else None)
    result["first_violation_row"] = next((r["decision_row"] for r in rows if r["verdict"]=="FAIL"), None)
    return result


def temporal_folds(samples, windows, *, embargo=timedelta(0), walk_forward=True):
    """Purge overlapping closed label intervals; embargo follows each test window."""
    if embargo < timedelta(0):
        raise ValueError("Embargo must be nonnegative")
    spans = [(instant(r["start"]), instant(r["label_end"])) for r in samples]
    if any(end < start for start, end in spans):
        raise ValueError("Reversed label interval")
    for raw_start, raw_end in windows:
        start, end = instant(raw_start), instant(raw_end)
        if end < start:
            raise ValueError("Reversed validation window")
        test = [i for i,(s,_) in enumerate(spans) if start <= s <= end]
        if not test:
            raise ValueError("Empty validation window")
        horizon = max([end]+[spans[i][1] for i in test])
        train = [i for i,(s,e) in enumerate(spans)
                 if e < start or (not walk_forward and s > horizon + embargo)]
        yield dict(train=train, test=test)


def revision_statistics(index, series, period):
    rows = index.revisions(series, period)
    delta = [number(r["delta"]) for r in rows[1:]]
    return dict(count=len(delta), mean=str(mean(delta)) if delta else None,
                median=str(median(delta)) if delta else None,
                mean_absolute=str(mean(map(abs,delta))) if delta else None,
                rms=str((sum(x*x for x in delta)/len(delta)).sqrt()) if delta else None)


def information_set(index, at, keys):
    """Preserve explicit missing members of a requested information set."""
    return [dict(series=s, period=p, evidence=index.asof(s,p,at)) for s,p in keys]


def compare_providers(left, right, at, keys, *, tolerance=0):
    tol = number(tolerance)
    if tol < 0:
        raise ValueError("Negative tolerance")
    result = []
    for s,p in keys:
        a, b = left.asof(s,p,at), right.asof(s,p,at)
        diff = number(a["value"])-number(b["value"]) if a and b else None
        verdict = "UNKNOWN" if diff is None else "PASS" if abs(diff)<=tol else "FAIL"
        result.append(dict(series=s, period=p, left=a, right=b, verdict=verdict,
                           delta=str(diff) if diff is not None else None))
    return result


def scientific_report(audit_result, *, dataset_versions, parameters):
    """Hash the full JSON evidence, method version, input manifests and parameters."""
    from . import __version__ as software
    payload = dict(schema=1, software="vintagecheck "+software, datasets=dataset_versions,
                   parameters=parameters, result=audit_result)
    payload = json.loads(json.dumps(payload, default=_json_value, allow_nan=False))
    return dict(payload, report_sha256=_digest(payload))
