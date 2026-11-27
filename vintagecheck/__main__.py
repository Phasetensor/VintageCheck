import argparse
import csv
import json
from decimal import InvalidOperation

from . import audit


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description="Audit a backtest against historical releases")
    parser.add_argument("releases")
    parser.add_argument("decisions")
    parser.add_argument("--threshold", required=True)
    args = parser.parse_args()
    try:
        report = audit(read_rows(args.releases), read_rows(args.decisions), args.threshold)
    except (KeyError, ValueError, OSError, InvalidOperation, AttributeError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
