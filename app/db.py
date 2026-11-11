from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

Pool = ConnectionPool[Connection[DictRow]]


def create_pool() -> Pool:
    """Create and open a connection pool. Called once per app lifespan."""
    pool: Pool = ConnectionPool(
        settings.database_url,
        connection_class=Connection[DictRow],
        kwargs={
            "row_factory": dict_row,
            # Set here, not in DATABASE_URL: the loader shares that URL and its one long
            # transaction must not be cut short.
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            # Never prepare statements server-side, so every query is planned with its real
            # values. A cached generic plan cannot tell a rare country from a common one.
            "prepare_threshold": None,
        },
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout_seconds,
        # Test each connection before lending it and replace it if the server closed it (a
        # restart, an idle timeout). Without this the first request after a database restart
        # fails although the database is up. Costs one extra round trip per request.
        check=ConnectionPool.check_connection,
        open=False,
    )
    pool.open(wait=True)
    return pool


def get_conn(request: Request) -> Iterator[Connection[DictRow]]:
    """FastAPI dependency that borrows a connection from the pool for one request."""
    pool: Pool = request.app.state.pool
    with pool.connection() as conn:
        yield conn


Conn = Annotated[Connection[DictRow], Depends(get_conn)]
