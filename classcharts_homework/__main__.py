"""Smoke-check retrieval without printing personal data, counts, or dates."""

import argparse
import sys

from .client import ClassChartsError, StudentClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check ClassCharts homework retrieval privately.")
    parser.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--display-date", choices=("due_date", "issue_date"), default="due_date")
    args = parser.parse_args(argv)
    try:
        with StudentClient.from_env() as client:
            client.get_homework(from_date=args.from_date, to_date=args.to_date,
                                display_date=args.display_date)
    except ClassChartsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Homework retrieval succeeded. No personal data was written or displayed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
