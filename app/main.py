from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from psycopg import Connection
from psycopg.rows import DictRow
from pydantic import BaseModel

from app.db import create_pool, get_conn


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.pool = create_pool()
    yield
    app.state.pool.close()


app = FastAPI(title="Coffee Health API", lifespan=lifespan)

Conn = Annotated[Connection[DictRow], Depends(get_conn)]


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


# TODO (Part 2): add the endpoint(s) that serve the coffee health records.
