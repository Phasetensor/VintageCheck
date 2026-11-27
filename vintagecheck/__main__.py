import argparse
import sys
import csv
import json
from decimal import InvalidOperation

from . import audit


from .io import read_rows


def main():
    parser = argparse.ArgumentParser(description="Audit a backtest against historical releases")
    parser.add_argument("releases")
    parser.add_argument("decisions")
    parser.add_argument("--threshold", required=True)
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args()
    try:
        report = audit(read_rows(args.releases), read_rows(args.decisions), args.threshold)
    except (KeyError, ValueError, OSError, InvalidOperation, AttributeError, TypeError) as error:
        parser.error(str(error))
    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        columns = ("decision_row", "series", "period", "decision_at", "verdict", "status",
                   "used_available_at", "asof_available_at", "used_value", "asof_value", "signal_changed")
        writer = csv.DictWriter(sys.stdout, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["decisions"])


if __name__ == "__main__":
    main()
