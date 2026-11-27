"""Compose the vintage and feature checks into a reproducible scientific report."""
import json

from vintagecheck.research import audit_backtest, dataset_version, scientific_report


def main():
    releases = [dict(id="gdp-first", series="GDP", period="Q1", value="1.6",
                     available_at="2024-04-25T12:30:00Z")]
    nodes = [dict(id="gdp-first", available_at=releases[0]["available_at"]),
             dict(id="scaled", parents=["gdp-first"], fitted=True,
                  available_at="2024-04-30T16:00:00Z", fit_data_end="2024-06-01T16:00:00Z")]
    decisions = [dict(id="rebalance", series="GDP", period="Q1", features=["scaled"],
                      decision_at="2024-05-01T16:00:00Z", used_available_at=releases[0]["available_at"])]
    inputs = dict(releases=releases, decisions=decisions, nodes=nodes)
    manifests = {name: dataset_version(rows, "id") for name, rows in inputs.items()}
    result = audit_backtest(**inputs, threshold="1.5")
    print(json.dumps(scientific_report(result, dataset_versions=manifests,
                                       parameters={"threshold":"1.5"}), indent=2))


if __name__ == "__main__":
    main()
