from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

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
