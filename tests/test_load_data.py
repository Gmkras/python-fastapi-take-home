"""Loader integration tests against the real database.

Every test runs inside one transaction that the `db_conn` fixture rolls back, and uses IDs far
above the real dataset, so nothing here depends on or disturbs the loaded data. The CSV files
are written by the tests byte for byte, so BOM and line endings are exactly what each test
says (files checked into git could have their line endings rewritten).
"""

from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.config import settings
from app.validation import EXPECTED_HEADER, HeaderError
from scripts import load_data
from scripts.load_data import ReconciliationError, format_summary, load_csv, main

Connection = psycopg.Connection[Any]

FIRST_ID = 9_000_000
BOM = b"\xef\xbb\xbf"

DEFAULTS: dict[str, str] = {
    "Age": "40",
    "Gender": "Male",
    "Country": "Germany",
    "Coffee_Intake": "3.5",
    "Caffeine_mg": "328.1",
    "Sleep_Hours": "7.5",
    "Sleep_Quality": "Good",
    "BMI": "24.9",
    "Heart_Rate": "78",
    "Stress_Level": "Low",
    "Physical_Activity_Hours": "14.5",
    "Health_Issues": "None",
    "Occupation": "Other",
    "Smoking": "0",
    "Alcohol_Consumption": "0",
}


def line(record_id: int, *, quoted: bool = False, **overrides: str) -> str:
    values = {"ID": str(record_id)} | DEFAULTS | overrides
    fields = [values[column] for column in EXPECTED_HEADER]
    if quoted:
        fields = [f'"{value}"' for value in fields]
    return ",".join(fields)


def write_csv(
    directory: Path,
    name: str,
    lines: list[str],
    *,
    bom: bool = True,
    newline: str = "\r\n",
    header: str = ",".join(EXPECTED_HEADER),
) -> Path:
    """Write header + lines. `lines[i]` ends up on file line i + 2."""
    path = directory / name
    text = newline.join([header, *lines]) + newline
    path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    return path


def record(conn: Connection, record_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute("select * from health_records where id = %s", (record_id,)).fetchone()
    assert row is not None, f"record {record_id} was not loaded"
    return row


def rejects(conn: Connection, source_file: str) -> dict[int, dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "select * from load_rejects where source_file = %s", (source_file,)
        ).fetchall()
    return {row["line_number"]: row for row in rows}


def count(conn: Connection, table: str) -> int:
    row = conn.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(table))).fetchone()
    assert row is not None
    return int(row[0])


# --- US1: valid rows are loaded normalized -----------------------------------------------


def test_valid_rows_are_loaded_normalized(db_conn: Connection, tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "test_valid.csv",
        [
            line(FIRST_ID),
            line(FIRST_ID + 1, quoted=True),
            line(FIRST_ID + 2, Country=" Germany  ", Gender="MALE", Smoking="Y"),
            line(FIRST_ID + 3, Age=""),
            line(FIRST_ID + 4, BMI="n/a"),
        ],
    )

    summary = load_csv(db_conn, path)

    assert (summary.read, summary.blank, summary.valid, summary.rejected) == (5, 0, 5, 0)
    assert summary.fixed == 1  # quoting alone is not a fix
    assert summary.with_missing == 2
    assert (summary.inserted, summary.updated, summary.unchanged) == (5, 0, 0)

    normalized = record(db_conn, FIRST_ID + 2)
    assert normalized["country"] == "Germany"
    assert normalized["gender"] == "Male"
    assert normalized["smoking"] is True
    assert record(db_conn, FIRST_ID + 1)["heart_rate"] == 78
    assert record(db_conn, FIRST_ID + 3)["age"] is None
    assert record(db_conn, FIRST_ID + 4)["bmi"] is None
    assert record(db_conn, FIRST_ID)["health_issues"] == "None"


def test_bom_crlf_and_plain_lf_files_load_the_same(db_conn: Connection, tmp_path: Path) -> None:
    lines = [line(FIRST_ID), line(FIRST_ID + 1, Country="uk"), line(FIRST_ID + 2, Age="x")]
    with_bom = write_csv(tmp_path, "test_bom_crlf.csv", lines, bom=True, newline="\r\n")
    plain = write_csv(tmp_path, "test_plain_lf.csv", lines, bom=False, newline="\n")

    first = load_csv(db_conn, with_bom)
    second = load_csv(db_conn, plain)

    assert (first.read, first.rejected, first.inserted) == (3, 1, 2)
    # Same content: the second file finds every record already there and identical.
    assert (second.read, second.rejected, second.inserted, second.updated) == (3, 1, 0, 0)
    assert second.unchanged == 2
    assert (
        rejects(db_conn, "test_bom_crlf.csv").keys() == rejects(db_conn, "test_plain_lf.csv").keys()
    )


# --- US2: every row that is not loaded is explained --------------------------------------


def test_rejected_rows_are_recorded_with_line_and_reason(
    db_conn: Connection, tmp_path: Path
) -> None:
    truncated = ",".join(line(FIRST_ID + 2).split(",")[:13])
    thirty = line(FIRST_ID + 3, Age="thirty")
    path = write_csv(
        tmp_path,
        "test_rejects.csv",
        [
            line(FIRST_ID),  # line 2
            "",  # line 3: blank, skipped and not recorded
            line(FIRST_ID + 1, quoted=True),  # line 4
            truncated,  # line 5
            thirty,  # line 6
            line(FIRST_ID + 4, Smoking="2"),  # line 7
            line(FIRST_ID + 5, Country=""),  # line 8
            line(FIRST_ID + 6),  # line 9: still loaded after the bad rows
        ],
    )

    summary = load_csv(db_conn, path)

    assert (summary.read, summary.blank, summary.rejected, summary.inserted) == (7, 1, 4, 3)
    assert summary.rejected_by_reason == {
        "column_count": 1,
        "not_numeric": 1,
        "invalid_boolean": 1,
        "missing_required": 1,
    }
    recorded = rejects(db_conn, "test_rejects.csv")
    assert sorted(recorded) == [5, 6, 7, 8]
    assert recorded[5]["reason"] == "column_count"
    assert recorded[5]["column_name"] is None
    assert recorded[5]["raw_fields"] == truncated.split(",")
    assert (recorded[6]["reason"], recorded[6]["column_name"], recorded[6]["raw_value"]) == (
        "not_numeric",
        "Age",
        "thirty",
    )
    assert recorded[6]["raw_fields"] == thirty.split(",")
    assert (recorded[7]["reason"], recorded[7]["column_name"], recorded[7]["raw_value"]) == (
        "invalid_boolean",
        "Smoking",
        "2",
    )
    assert (recorded[8]["reason"], recorded[8]["column_name"], recorded[8]["raw_value"]) == (
        "missing_required",
        "Country",
        "",
    )
    assert record(db_conn, FIRST_ID + 6)["id"] == FIRST_ID + 6
    assert summary.rejects_in_table == 4


def test_nul_characters_in_a_rejected_row_do_not_break_the_load(
    db_conn: Connection, tmp_path: Path
) -> None:
    path = write_csv(
        tmp_path, "test_nul.csv", [line(FIRST_ID, Country="Ger\x00many"), line(FIRST_ID + 1)]
    )

    summary = load_csv(db_conn, path)

    assert (summary.rejected, summary.inserted) == (1, 1)
    recorded = rejects(db_conn, "test_nul.csv")[2]
    assert recorded["reason"] == "unknown_country"
    assert recorded["raw_value"] == "Germany"  # PostgreSQL text cannot store the NUL itself


def test_summary_is_printed_in_the_documented_format(db_conn: Connection, tmp_path: Path) -> None:
    path = write_csv(tmp_path, "test_summary.csv", [line(FIRST_ID), line(FIRST_ID + 1, Age="x")])

    text = format_summary(load_csv(db_conn, path))

    lines = text.splitlines()
    assert lines[0].startswith("Loaded ") and lines[0].endswith("test_summary.csv")
    assert [entry.split() for entry in lines[1:7]] == [
        ["rows", "read", "2"],
        ["blank", "lines", "0"],
        ["valid", "1"],
        ["fixed", "0"],
        ["with", "missing", "values", "0"],
        ["rejected", "1"],
    ]
    assert "    not_numeric" in text
    assert lines[-1].startswith("Elapsed ")


# --- US3: duplicate IDs ------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", [50_000, 3, 1])
def test_last_valid_occurrence_of_an_id_wins(
    db_conn: Connection, tmp_path: Path, chunk_size: int
) -> None:
    a, b, c, d, e = range(FIRST_ID, FIRST_ID + 5)
    padded = line(c, Country=" Germany  ")
    path = write_csv(
        tmp_path,
        "test_duplicates.csv",
        [
            line(a),  # line 2: same as line 3 -> exact duplicate
            line(a),  # line 3: wins
            line(b, Age="30"),  # line 4: differs from line 5 -> conflict
            line(b, Age="31"),  # line 5: wins
            padded,  # line 6: equals line 8 once normalized -> exact duplicate
            line(c, Heart_Rate="80"),  # line 7: conflict
            line(c),  # line 8: wins
            line(d, Age="50"),  # line 9: wins, the later occurrence is invalid
            line(d, Age="thirty"),  # line 10: rejected, not a duplicate
            line(e),  # line 11: conflict, it differs only in a value that is missing later
            line(e, BMI=""),  # line 12: wins, even with the missing value
        ],
    )

    summary = load_csv(db_conn, path, chunk_size=chunk_size)

    assert (summary.read, summary.rejected, summary.valid) == (11, 1, 10)
    assert (summary.duplicate_exact, summary.duplicate_conflict) == (2, 3)
    assert (summary.loaded, summary.inserted) == (5, 5)

    assert record(db_conn, b)["age"] == 31
    assert record(db_conn, c)["heart_rate"] == 78
    assert record(db_conn, d)["age"] == 50
    assert record(db_conn, e)["bmi"] is None

    recorded = rejects(db_conn, "test_duplicates.csv")
    assert {number: row["reason"] for number, row in recorded.items()} == {
        2: "duplicate_exact",
        4: "duplicate_conflict",
        6: "duplicate_exact",
        7: "duplicate_conflict",
        10: "not_numeric",
        11: "duplicate_conflict",
    }
    assert recorded[2]["detail"] == "superseded by line 3"
    assert recorded[4]["detail"] == "superseded by line 5"
    assert recorded[6]["detail"] == "superseded by line 8"
    assert recorded[7]["detail"] == "superseded by line 8"
    assert recorded[11]["detail"] == "superseded by line 12"
    # A discarded duplicate keeps the row as it was in the file, not the normalized values.
    assert recorded[6]["raw_fields"] == padded.split(",")
    assert recorded[4]["column_name"] is None


# --- US4: re-running a load --------------------------------------------------------------


def test_loading_the_same_file_twice_changes_nothing(db_conn: Connection, tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "test_idempotent.csv",
        [line(FIRST_ID), line(FIRST_ID + 1), line(FIRST_ID + 1), line(FIRST_ID + 2, Age="x")],
    )

    first = load_csv(db_conn, path)
    records_after_first = count(db_conn, "health_records")
    rejects_after_first = count(db_conn, "load_rejects")
    second = load_csv(db_conn, path)

    assert (first.inserted, first.rejects_in_table) == (2, 2)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 2)
    assert second.rejects_in_table == 2
    assert count(db_conn, "health_records") == records_after_first
    assert count(db_conn, "load_rejects") == rejects_after_first


def test_reloading_a_changed_file_updates_by_id(db_conn: Connection, tmp_path: Path) -> None:
    original = write_csv(tmp_path, "test_valid.csv", [line(FIRST_ID), line(FIRST_ID + 1)])
    changed = write_csv(
        tmp_path, "test_valid_changed.csv", [line(FIRST_ID), line(FIRST_ID + 1, Heart_Rate="99")]
    )

    load_csv(db_conn, original)
    summary = load_csv(db_conn, changed)

    assert (summary.inserted, summary.updated, summary.unchanged) == (0, 1, 1)
    assert record(db_conn, FIRST_ID + 1)["heart_rate"] == 99


def test_invalid_row_never_touches_an_existing_record(db_conn: Connection, tmp_path: Path) -> None:
    good = write_csv(tmp_path, "test_good.csv", [line(FIRST_ID, Age="33")])
    bad = write_csv(tmp_path, "test_bad.csv", [line(FIRST_ID, Age="thirty")])

    load_csv(db_conn, good)
    summary = load_csv(db_conn, bad)

    assert (summary.rejected, summary.inserted, summary.updated) == (1, 0, 0)
    assert record(db_conn, FIRST_ID)["age"] == 33
    assert rejects(db_conn, "test_bad.csv")[2]["reason"] == "not_numeric"


def test_rejects_of_other_files_are_kept_and_never_duplicated(
    db_conn: Connection, tmp_path: Path
) -> None:
    first = write_csv(tmp_path, "test_a.csv", [line(FIRST_ID, Age="x")])
    second = write_csv(tmp_path, "test_b.csv", [line(FIRST_ID + 1, Age="y")])

    load_csv(db_conn, first)
    load_csv(db_conn, second)
    load_csv(db_conn, first)

    assert sorted(rejects(db_conn, "test_a.csv")) == [2]
    assert sorted(rejects(db_conn, "test_b.csv")) == [2]


def test_unexpected_header_stops_before_changing_anything(
    db_conn: Connection, tmp_path: Path
) -> None:
    load_csv(db_conn, write_csv(tmp_path, "test_header.csv", [line(FIRST_ID, Age="x")]))
    records_before = count(db_conn, "health_records")
    broken = write_csv(
        tmp_path,
        "test_header.csv",
        [line(FIRST_ID + 1)],
        header=",".join(EXPECTED_HEADER[:-1]),
    )

    with pytest.raises(HeaderError):
        load_csv(db_conn, broken)

    assert count(db_conn, "health_records") == records_before
    assert sorted(rejects(db_conn, "test_header.csv")) == [2]  # the earlier load's, untouched


def test_failed_load_leaves_the_database_as_it_was(
    db_conn: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load_csv(db_conn, write_csv(tmp_path, "test_before.csv", [line(FIRST_ID, Age="x")]))
    records_before = count(db_conn, "health_records")
    rejects_before = count(db_conn, "load_rejects")
    failing = write_csv(
        tmp_path, "test_failing.csv", [line(FIRST_ID + 1), line(FIRST_ID + 2, Age="y")]
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise ReconciliationError("forced failure after the merge")

    monkeypatch.setattr(load_data, "_verify", fail)

    # The savepoint stands in for the loader's own transaction: main() rolls back on error.
    with pytest.raises(ReconciliationError), db_conn.transaction():
        load_csv(db_conn, failing)

    assert count(db_conn, "health_records") == records_before
    assert count(db_conn, "load_rejects") == rejects_before
    assert rejects(db_conn, "test_failing.csv") == {}


@pytest.mark.parametrize(
    ("column", "value", "error"),
    [
        ("age", 150, psycopg.errors.CheckViolation),
        ("age", 17, psycopg.errors.CheckViolation),
        ("gender", "x", psycopg.errors.CheckViolation),
        ("heart_rate", 0, psycopg.errors.CheckViolation),
        ("bmi", 5, psycopg.errors.CheckViolation),
        ("coffee_intake", 21, psycopg.errors.CheckViolation),
        ("id", 0, psycopg.errors.CheckViolation),
        ("country", "  ", psycopg.errors.CheckViolation),
        ("country", None, psycopg.errors.NotNullViolation),
        ("caffeine_mg", None, psycopg.errors.NotNullViolation),
    ],
)
def test_database_rejects_invalid_values_on_its_own(
    db_conn: Connection, column: str, value: object, error: type[psycopg.Error]
) -> None:
    values: dict[str, object] = {
        "id": FIRST_ID,
        "country": "Germany",
        "coffee_intake": 1,
        "caffeine_mg": 100,
    } | {column: value}
    insert = sql.SQL("insert into health_records ({}) values ({})").format(
        sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Placeholder() * len(values)),
    )

    with pytest.raises(error), db_conn.transaction():
        db_conn.execute(insert, list(values.values()))


# --- command line: fatal errors exit with 1 and change nothing ----------------------------


def test_main_reports_a_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([str(tmp_path / "does_not_exist.csv")])

    assert exit_code == 1
    assert "error: cannot read" in capsys.readouterr().err


def test_main_reports_an_unexpected_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = write_csv(tmp_path, "test_broken.csv", [line(FIRST_ID)], header="ID,Age")

    exit_code = main([str(broken)])

    assert exit_code == 1
    assert "error: unexpected header" in capsys.readouterr().err


def test_main_reports_an_unreachable_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_csv(tmp_path, "test_unreachable.csv", [line(FIRST_ID)])
    monkeypatch.setattr(
        settings, "database_url", "postgresql://postgres:postgres@127.0.0.1:1/coffee"
    )
    monkeypatch.setattr(load_data, "CONNECT_TIMEOUT_SECONDS", 2)  # do not wait the full default

    exit_code = main([str(path)])

    assert exit_code == 1
    assert "error: cannot connect to database" in capsys.readouterr().err
