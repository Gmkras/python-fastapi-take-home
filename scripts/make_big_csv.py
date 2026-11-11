"""Build a large CSV for scale measurements by repeating a source CSV.

Usage:
    uv run python scripts/make_big_csv.py data/coffee_health_raw.csv 100 big.csv

Every copy shifts the numeric IDs by 10,000,000 so copies do not collide with each other.
Everything else is kept byte for byte (BOM, line endings, quoting, dirty rows), so the result
exercises the loader exactly like the real file, only bigger. Rows are streamed: memory use
does not depend on the number of copies.
"""

import argparse
import sys
from pathlib import Path

ID_STEP = 10_000_000
# Above this the shifted IDs no longer fit a PostgreSQL integer and the loader would reject
# them as out of range, which would measure rejections instead of loads.
MAX_COPIES = 214


def shift_id(line: str, offset: int) -> str:
    """Add `offset` to the ID (first field) of a CSV line, if that field is a plain number."""
    first, separator, rest = line.partition(",")
    quote = '"' if first.startswith('"') and first.endswith('"') and len(first) >= 2 else ""
    digits = first.strip('"') if quote else first
    if not separator or not (digits.isascii() and digits.isdigit()):
        return line
    return f"{quote}{int(digits) + offset}{quote}{separator}{rest}"


def make_big_csv(source: Path, copies: int, destination: Path) -> int:
    """Write `copies` shifted copies of `source` to `destination`. Returns data lines written."""
    written = 0
    with destination.open("w", encoding="utf-8-sig", newline="") as output:
        for copy in range(copies):
            with source.open(encoding="utf-8-sig", newline="") as lines:
                header = next(lines, "")
                if copy == 0:
                    output.write(header)
                for line in lines:
                    output.write(shift_id(line, copy * ID_STEP))
                    written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("copies", type=int)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)

    if not 1 <= args.copies <= MAX_COPIES:
        print(f"error: copies must be between 1 and {MAX_COPIES}", file=sys.stderr)
        return 2
    written = make_big_csv(args.source, args.copies, args.destination)
    print(f"Wrote {written} lines to {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
