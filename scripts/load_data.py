"""Load the coffee health CSV into Postgres.

Usage:
    uv run python scripts/load_data.py data/coffee_health_raw.csv

TODO (Part 1): implement this. See README.md for the requirements.
"""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()
    raise SystemExit(f"Not implemented yet: would load {args.csv_path}")


if __name__ == "__main__":
    main()
