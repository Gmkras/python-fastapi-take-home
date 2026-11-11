from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from pydantic import BaseModel

from app.db import Conn, create_pool
from app.records import router as records_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.pool = create_pool()
    yield
    app.state.pool.close()


app = FastAPI(title="Coffee Health API", lifespan=lifespan)
app.include_router(records_router)


@app.exception_handler(OperationalError)
def database_unavailable(request: Request, error: OperationalError) -> JSONResponse:
    """Answer 503 when the database cannot serve the request right now.

    OperationalError covers the three temporary cases: no free connection within the pool
    timeout (PoolTimeout), a query cancelled by the statement time limit (QueryCanceled) and
    a lost connection. Mistakes in a query are a different class and still surface as 500.
    """
    return JSONResponse(
        {"detail": "Database is busy or unavailable; retry shortly."}, status_code=503
    )


class Health(BaseModel):
    status: str
    database: str


class Note(BaseModel):
    id: int
    title: str


@app.get("/health")
def health(conn: Conn) -> Health:
    conn.execute("select 1")
    return Health(status="ok", database="ok")


@app.get("/notes")
def list_notes(conn: Conn) -> list[Note]:
    """Example endpoint backed by the sample `notes` table. Keep, replace or remove it."""
    rows = conn.execute("select id, title from notes order by id").fetchall()
    return [Note.model_validate(row) for row in rows]
