"""Run explicitly attributed local UCI experiments, using only proofline_dev."""
import argparse
import json
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from app.config import database_url
from app.datasets.source import read_subset
from app.datasets.download import acquire_workbook
from app.datasets.service import import_subset, EXPERIMENTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=Path("work/uci352/Online Retail.xlsx"))
    parser.add_argument("--download", action="store_true", help="Acquire the pinned workbook from the official UCI host if absent")
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--experiment", choices=sorted(EXPERIMENTS), default="real")
    args = parser.parse_args()
    engine = None
    try:
        url = make_url(database_url())
        if url.database != "proofline_dev":
            raise ValueError("Dataset CLI is restricted to proofline_dev; integration tests use fixture-owned test schemas")
        workbook = acquire_workbook(args.workbook.parent) if args.download else args.workbook
        subset = read_subset(workbook, args.size)
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        result = import_subset(engine, subset, args.experiment)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    except Exception as error:
        # Credentials, original customer values, and arbitrary DB exception strings stay private.
        print(json.dumps({"error": type(error).__name__, "message": "Import failed. Check the trusted workbook, configured development database, and migrations. Credentials withheld."}))
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            engine.dispose()

if __name__ == "__main__":
    main()
