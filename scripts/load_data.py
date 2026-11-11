"""Load the coffee health CSV into Postgres.

Usage:
    uv run python scripts/load_data.py data/coffee_health_raw.csv

The file is read as a stream and validated row by row (app/validation.py). Valid rows go to a
temporary staging table with COPY; rows that cannot be loaded go to load_rejects with their
line number and reason. Duplicate IDs are then resolved in SQL (the last valid occurrence
wins) and the winners are merged into health_records, writing only what changed.

Everything happens in one transaction that is committed only if the counts reconcile, so a
failed load leaves the database untouched and re-running a load never duplicates data.
"""

import argparse
import csv
import json
import sys
import time
import tracemalloc
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import batched
from operator import attrgetter
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

# `python scripts/load_data.py` puts scripts/, not the repository root, on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.validation import (  # noqa: E402
    FIELD_NAMES,
    HeaderError,
    Rejection,
    RejectReason,
    check_header,
    parse_row,
)

# Measured with 1M rows: 10,000 peaks at 65 MiB and 50,000 at 142 MiB, at the same rows/s.
DEFAULT_CHUNK_SIZE = 10_000
CONNECT_TIMEOUT_SECONDS = 10

# Arbitrary constant: every loader takes the same advisory lock, so loads run one at a time.
_LOAD_LOCK_KEY = 4_815_162_342

_DATA_FIELDS = FIELD_NAMES[1:]  # every column except the id

# The record's values in column order. Category members are StrEnum, i.e. str, and are sent
# to the database as their text value.
_RECORD_VALUES = attrgetter(*FIELD_NAMES)


def _columns(names: Sequence[str], alias: str | None = None) -> sql.Composed:
    identifiers = (sql.Identifier(alias, name) if alias else sql.Identifier(name) for name in names)
    return sql.SQL(", ").join(identifiers)


# The staging table takes its column types from health_records, so the two cannot drift.
# raw_row keeps the original fields of the line as one JSON text: a single text value is far
# cheaper to COPY than a text[] and is only unpacked for the few rows that end up rejected.
# DROP IF EXISTS lets a second load run inside the same transaction (the tests do that).
_CREATE_STAGING = sql.SQL(
    """
    create temp table _staging on commit drop as
    select 0 as line_no, {columns}, ''::text as raw_row
    from health_records
    with no data
    """
).format(columns=_columns(FIELD_NAMES))

_COPY_STAGING = sql.SQL("copy _staging (line_no, {columns}, raw_row) from stdin").format(
    columns=_columns(FIELD_NAMES)
)

_COPY_REJECTS = sql.SQL(
    """
    copy load_rejects
        (source_file, line_number, reason, column_name, raw_value, detail, raw_fields)
    from stdin
    """
)

# Earlier occurrences of an ID lose to the last one (highest line number). They are recorded
# in load_rejects and removed from staging, which leaves exactly one row per ID to merge.
# Only IDs that repeat are ranked, so the sort is over the duplicates, not the whole file.
# IS NOT DISTINCT FROM compares NULLs as equal, which plain = would not.
_RECORD_DUPLICATES = sql.SQL(
    """
    with repeated as (
        select id from _staging group by id having count(*) > 1
    ),
    ranked as (
        select staged.*,
               row_number() over (partition by staged.id order by staged.line_no desc) as recency
        from _staging as staged
        join repeated using (id)
    ),
    recorded as (
        insert into load_rejects (source_file, line_number, reason, detail, raw_fields)
        select %s,
               loser.line_no,
               case when ({loser}) is not distinct from ({winner})
                    then {exact} else {conflict} end,
               'superseded by line ' || winner.line_no,
               array(
                   select field
                   from jsonb_array_elements_text(loser.raw_row::jsonb)
                        with ordinality as fields (field, position)
                   order by position
               )
        from ranked as loser
        join ranked as winner on winner.id = loser.id and winner.recency = 1
        where loser.recency > 1
        returning line_number, reason
    ),
    removed as (
        delete from _staging as staged using recorded
        where staged.line_no = recorded.line_number
    )
    select reason, count(*) from recorded group by reason
    """
).format(
    loser=_columns(_DATA_FIELDS, "loser"),
    winner=_columns(_DATA_FIELDS, "winner"),
    exact=sql.Literal(RejectReason.DUPLICATE_EXACT.value),
    conflict=sql.Literal(RejectReason.DUPLICATE_CONFLICT.value),
)

# Upsert what is left in staging (one row per ID). Rows identical to what is already stored
# are not rewritten, which is what makes a repeated load a no-op. merge_action() tells
# inserts from updates.
_MERGE_STAGED = sql.SQL(
    """
    with merged as (
        merge into health_records as target
        using _staging as source
        on target.id = source.id
        when matched and ({target_data}) is distinct from ({source_data}) then
            update set {assignments}, loaded_at = now()
        when not matched then
            insert ({columns}) values ({source_columns})
        returning merge_action() as action
    )
    select action, count(*) from merged group by action
    """
).format(
    columns=_columns(FIELD_NAMES),
    source_columns=_columns(FIELD_NAMES, "source"),
    target_data=_columns(_DATA_FIELDS, "target"),
    source_data=_columns(_DATA_FIELDS, "source"),
    assignments=sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(name), sql.Identifier("source", name))
        for name in _DATA_FIELDS
    ),
)


class ReconciliationError(RuntimeError):
    """The counts of a load do not add up; nothing must be committed."""


@dataclass
class LoadSummary:
    """What one load did. Printed at the end; not stored."""

    source: str
    source_file: str
    read: int = 0
    blank: int = 0
    fixed: int = 0
    with_missing: int = 0
    rejected_by_reason: Counter[str] = field(default_factory=Counter)
    duplicate_exact: int = 0
    duplicate_conflict: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rows_in_table: int = 0
    rejects_in_table: int = 0
    elapsed_seconds: float = 0.0
    peak_memory_bytes: int | None = None

    @property
    def rejected(self) -> int:
        return sum(self.rejected_by_reason.values())

    @property
    def valid(self) -> int:
        return self.read - self.rejected

    @property
    def duplicates(self) -> int:
        return self.duplicate_exact + self.duplicate_conflict

    @property
    def loaded(self) -> int:
        """Rows that reached health_records: valid rows minus superseded duplicates."""
        return self.valid - self.duplicates


def load_csv(
    conn: psycopg.Connection[Any], csv_path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> LoadSummary:
    """Load one CSV file. Does not commit: the caller owns the transaction."""
    started = time.perf_counter()
    summary = LoadSummary(source=csv_path.as_posix(), source_file=csv_path.name)

    with (
        csv_path.open(encoding="utf-8-sig", newline="") as handle,
        conn.cursor(row_factory=tuple_row) as cur,
    ):
        reader = csv.reader(handle)
        check_header(next(reader, []))

        def data_rows() -> Iterator[tuple[int, list[str]]]:
            for fields in reader:
                if not fields or (len(fields) == 1 and not fields[0].strip()):
                    summary.blank += 1
                    continue
                summary.read += 1
                yield reader.line_num, fields

        cur.execute("select pg_advisory_xact_lock(%s)", (_LOAD_LOCK_KEY,))
        rows_before = _count(cur, sql.SQL("select count(*) from health_records"))
        cur.execute("delete from load_rejects where source_file = %s", (summary.source_file,))
        cur.execute("drop table if exists _staging")
        cur.execute(_CREATE_STAGING)

        # One chunk in memory at a time: memory use does not depend on the file size.
        for chunk in batched(data_rows(), chunk_size):
            rejections = _stage_valid_rows(cur, chunk, summary)
            _record_rejections(cur, summary.source_file, rejections)

        staged = _count(cur, sql.SQL("select count(*) from _staging"))

        duplicates: dict[str, int] = dict(
            cur.execute(_RECORD_DUPLICATES, (summary.source_file,)).fetchall()
        )
        summary.duplicate_exact = duplicates.get(RejectReason.DUPLICATE_EXACT.value, 0)
        summary.duplicate_conflict = duplicates.get(RejectReason.DUPLICATE_CONFLICT.value, 0)

        winners = _count(cur, sql.SQL("select count(*) from _staging"))  # one row per ID now

        actions: dict[str, int] = dict(cur.execute(_MERGE_STAGED).fetchall())
        summary.inserted = actions.get("INSERT", 0)
        summary.updated = actions.get("UPDATE", 0)
        summary.unchanged = winners - summary.inserted - summary.updated

        summary.rows_in_table = _count(cur, sql.SQL("select count(*) from health_records"))
        summary.rejects_in_table = _count(
            cur,
            sql.SQL("select count(*) from load_rejects where source_file = %s"),
            (summary.source_file,),
        )

    summary.elapsed_seconds = time.perf_counter() - started
    _verify(summary, staged=staged, winners=winners, rows_before=rows_before)
    return summary


def _stage_valid_rows(
    cur: psycopg.Cursor[tuple[Any, ...]],
    chunk: Sequence[tuple[int, list[str]]],
    summary: LoadSummary,
) -> list[Rejection]:
    """Validate a chunk, COPY its valid rows to staging and return its rejections."""
    rejections: list[Rejection] = []
    with cur.copy(_COPY_STAGING) as copy:
        for line_number, fields in chunk:
            result = parse_row(line_number, fields)
            if isinstance(result, Rejection):
                summary.rejected_by_reason[result.reason.value] += 1
                rejections.append(result)
                continue
            summary.fixed += bool(result.fixed)
            summary.with_missing += bool(result.missing)
            copy.write_row((line_number, *_RECORD_VALUES(result.record), _raw_row(fields)))
    return rejections


def _record_rejections(
    cur: psycopg.Cursor[tuple[Any, ...]], source_file: str, rejections: Sequence[Rejection]
) -> None:
    if not rejections:
        return
    with cur.copy(_COPY_REJECTS) as copy:
        for rejection in rejections:
            copy.write_row(
                (
                    source_file,
                    rejection.line_number,
                    rejection.reason.value,
                    rejection.column_name,
                    None if rejection.raw_value is None else _db_text(rejection.raw_value),
                    rejection.detail,
                    _db_text_list(rejection.raw_fields),
                )
            )


def _db_text(value: str) -> str:
    return value.replace("\x00", "")  # PostgreSQL text cannot hold NUL characters


def _db_text_list(values: Sequence[str]) -> list[str]:
    return [_db_text(value) for value in values]


def _raw_row(fields: Sequence[str]) -> str:
    """The original fields of a valid row as JSON text.

    No NUL handling needed here: a valid row matched a strict pattern or catalog in every
    field, so it cannot contain one.
    """
    return json.dumps(fields)


def _count(
    cur: psycopg.Cursor[tuple[Any, ...]], query: sql.SQL, params: Sequence[object] = ()
) -> int:
    row = cur.execute(query, params).fetchone()
    assert row is not None  # count(*) always returns one row
    return int(row[0])


def _verify(summary: LoadSummary, *, staged: int, winners: int, rows_before: int) -> None:
    """Cross-check what Python counted against what the database holds."""
    checks = {
        f"valid rows ({summary.valid}) != rows staged ({staged})": summary.valid == staged,
        (
            f"rows read ({summary.read}) != rejected ({summary.rejected})"
            f" + duplicates ({summary.duplicates}) + loaded ({winners})"
        ): summary.read == summary.rejected + summary.duplicates + winners,
        (
            f"loaded ({winners}) < inserted ({summary.inserted}) + updated ({summary.updated})"
        ): summary.unchanged >= 0,
        (
            f"rejects in table ({summary.rejects_in_table}) != rejected ({summary.rejected})"
            f" + duplicates ({summary.duplicates})"
        ): summary.rejects_in_table == summary.rejected + summary.duplicates,
        (
            f"rows in table ({summary.rows_in_table}) != rows before ({rows_before})"
            f" + inserted ({summary.inserted})"
        ): summary.rows_in_table == rows_before + summary.inserted,
    }
    failures = [message for message, holds in checks.items() if not holds]
    if failures:
        raise ReconciliationError("; ".join(failures))


def format_summary(summary: LoadSummary) -> str:
    lines = [f"Loaded {summary.source}"]

    def add(label: str, value: int, indent: int = 2) -> None:
        lines.append(f"{' ' * indent}{label:<{24 - indent}}{value:>8}")

    add("rows read", summary.read)
    add("blank lines", summary.blank)
    add("valid", summary.valid)
    add("fixed", summary.fixed, indent=4)
    add("with missing values", summary.with_missing, indent=4)
    add("rejected", summary.rejected)
    for reason, count in summary.rejected_by_reason.most_common():
        add(reason, count, indent=4)
    add("duplicates", summary.duplicates)
    add(RejectReason.DUPLICATE_EXACT.value, summary.duplicate_exact, indent=4)
    add(RejectReason.DUPLICATE_CONFLICT.value, summary.duplicate_conflict, indent=4)
    add("inserted", summary.inserted)
    add("updated", summary.updated)
    add("unchanged", summary.unchanged)
    add("rows in table", summary.rows_in_table)
    add("rejects in table", summary.rejects_in_table)

    rate = summary.read / summary.elapsed_seconds if summary.elapsed_seconds else 0.0
    lines.append(f"Elapsed {summary.elapsed_seconds:.1f} s ({rate:,.0f} rows/s)")
    if summary.peak_memory_bytes is not None:
        lines.append(f"Peak Python memory {summary.peak_memory_bytes / 2**20:.1f} MiB")
    return "\n".join(lines)


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the coffee health CSV into Postgres.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--chunk-size", type=_positive_int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument(
        "--profile-memory",
        action="store_true",
        help="report peak Python memory (slows the load down; for measuring only)",
    )
    args = parser.parse_args(argv)

    if args.profile_memory:
        tracemalloc.start()
    try:
        conn = psycopg.connect(settings.database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.Error as error:
        return _fail(f"cannot connect to database: {error}")

    try:
        # Leaving the block commits; an exception rolls everything back. Either way it closes.
        with conn:
            summary = load_csv(conn, args.csv_path, args.chunk_size)
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        return _fail(f"cannot read {args.csv_path}: {error}")
    except HeaderError as error:
        return _fail(str(error))
    except ReconciliationError as error:
        return _fail(f"reconciliation failed: {error}")
    except psycopg.Error as error:
        return _fail(f"database error: {error}")

    if args.profile_memory:
        summary.peak_memory_bytes = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
