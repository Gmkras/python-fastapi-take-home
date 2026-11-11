from collections.abc import Iterator, Sequence
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.config import settings
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Using TestClient as a context manager runs the app lifespan (opens the DB pool).
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection[Any]]:
    """A connection whose work is always rolled back, so tests leave no data behind."""
    conn = psycopg.connect(settings.database_url, connect_timeout=10)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def record_row(record_id: int, **overrides: object) -> dict[str, object]:
    """A complete, valid health_records row. Override any column by name."""
    row: dict[str, object] = {
        "id": record_id,
        "age": 40,
        "gender": "Male",
        "country": "Testland",
        "coffee_intake": Decimal("3.5"),
        "caffeine_mg": Decimal("300.0"),
        "sleep_hours": Decimal("7.5"),
        "sleep_quality": "Good",
        "bmi": Decimal("24.9"),
        "heart_rate": 70,
        "stress_level": "Low",
        "physical_activity_hours": Decimal("5.0"),
        "health_issues": "None",
        "occupation": "Office",
        "smoking": False,
        "alcohol_consumption": False,
    }
    unknown = overrides.keys() - row.keys()
    assert not unknown, f"not columns of health_records: {sorted(unknown)}"
    return row | overrides


def reject_row(source_file: str, line_number: int, **overrides: object) -> dict[str, object]:
    """A complete load_rejects row. Override any column by name."""
    row: dict[str, object] = {
        "source_file": source_file,
        "line_number": line_number,
        "reason": "not_numeric",
        "column_name": "Age",
        "raw_value": "thirty",
        "detail": "Age='thirty': not_numeric",
        "raw_fields": ["1", "thirty"],
    }
    unknown = overrides.keys() - row.keys()
    assert not unknown, f"not columns of load_rejects: {sorted(unknown)}"
    return row | overrides


class Seed:
    """Inserts committed rows for API tests and deletes exactly those rows afterwards.

    The API reads through its own pooled connections, so it cannot see uncommitted data: the
    rollback isolation of `db_conn` does not work here.
    """

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn
        self._record_ids: list[int] = []
        self._reject_keys: list[tuple[str, int]] = []

    def records(self, rows: Sequence[dict[str, object]]) -> None:
        for row in rows:
            self._insert("health_records", row)
            self._record_ids.append(int(str(row["id"])))

    def rejects(self, rows: Sequence[dict[str, object]]) -> None:
        for row in rows:
            self._insert("load_rejects", row)
            self._reject_keys.append((str(row["source_file"]), int(str(row["line_number"]))))

    def cleanup(self) -> None:
        self._conn.execute("delete from health_records where id = any(%s)", (self._record_ids,))
        for source_file, line_number in self._reject_keys:
            self._conn.execute(
                "delete from load_rejects where source_file = %s and line_number = %s",
                (source_file, line_number),
            )

    def _insert(self, table: str, row: dict[str, object]) -> None:
        statement = sql.SQL("insert into {} ({}) values ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, row)),
            sql.SQL(", ").join(sql.Placeholder() * len(row)),
        )
        self._conn.execute(statement, list(row.values()))


@pytest.fixture
def seed() -> Iterator[Seed]:
    """Committed test rows, removed when the test ends."""
    with psycopg.connect(settings.database_url, connect_timeout=10, autocommit=True) as conn:
        seeded = Seed(conn)
        try:
            yield seeded
        finally:
            seeded.cleanup()
