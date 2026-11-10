from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Using TestClient as a context manager runs the app lifespan (opens the DB pool).
    with TestClient(app) as c:
        yield c
