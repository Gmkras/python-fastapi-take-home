"""Read-only routes over the loaded data. The only API module that runs SQL.

The conditions of every query come from the pure builders in app/filters.py; this module only
places them in a fixed statement and executes it.
"""

from typing import Annotated

from fastapi import APIRouter, Query
from psycopg import sql

from app.db import Conn
from app.filters import (
    GROUP_BY,
    RecordListParams,
    RejectListParams,
    RejectSummaryParams,
    SummaryParams,
    build_reject_where,
    build_where,
)
from app.schemas import (
    ReasonCount,
    Record,
    RecordPage,
    Reject,
    RejectCursor,
    RejectPage,
    SummaryRow,
)

router = APIRouter()


def _where(conditions: sql.Composable, values: list[object]) -> sql.Composable:
    """` where <conditions>`, or nothing when no condition was built."""
    return sql.SQL(" where ") + conditions if values else sql.SQL("")


def _page[Row](rows: list[Row], limit: int) -> tuple[list[Row], Row | None]:
    """Split the `limit + 1` rows that were fetched into the page and its cursor row.

    Asking for one row more than the page tells whether another page exists without a second
    query. The cursor row is the last one of the page, or None when nothing follows; that
    also covers a last page that is exactly full.
    """
    page = rows[:limit]
    return page, page[-1] if len(rows) > limit else None


# --- records -----------------------------------------------------------------------------

_RECORD_COLUMNS = sql.SQL(", ").join(sql.Identifier(name) for name in Record.model_fields)

# Keyset pagination: the primary key index jumps straight to the cursor, so a deep page costs
# the same as the first one. No OFFSET and no count(*).
_LIST_RECORDS = sql.SQL(
    "select {columns} from health_records where id > %s{filters} order by id limit %s"
)

# GROUP BY already makes a group of the rows whose key is NULL, and avg() already skips NULLs
# and yields NULL when there is nothing to average: missing data needs no special handling,
# and the counts always add up to the number of matching records.
_SUMMARIZE_RECORDS = sql.SQL(
    """
    select {key} as key,
           count(*) as count,
           round(avg(sleep_hours), 2) as avg_sleep_hours,
           round(avg(coffee_intake), 2) as avg_coffee_intake,
           round(avg(bmi), 2) as avg_bmi
    from health_records{filters}
    group by 1
    order by 1 nulls last
    """
)


@router.get("/records", summary="List records, one page at a time", tags=["records"])
def list_records(params: Annotated[RecordListParams, Query()], conn: Conn) -> RecordPage:
    """Records ordered by ID, at most `limit` per page (200 at most).

    To read the next page, repeat the request with `after_id` set to the `next_after_id` you
    received. It is null on the last page.

    Filters are combined with AND; a repeated parameter matches any of its values. A filter on
    a column leaves out the records that have no value in it.
    """
    where, values = build_where(params)
    query = _LIST_RECORDS.format(
        columns=_RECORD_COLUMNS,
        filters=sql.SQL(" and ") + where if values else sql.SQL(""),
    )
    rows = conn.execute(query, [params.after_id, *values, params.limit + 1]).fetchall()
    page, last = _page(rows, params.limit)
    return RecordPage(
        items=[Record.model_validate(row) for row in page],
        next_after_id=last["id"] if last else None,
        limit=params.limit,
    )


@router.get("/records/summary", summary="Count and averages per group", tags=["records"])
def summarize_records(params: Annotated[SummaryParams, Query()], conn: Conn) -> list[SummaryRow]:
    """Number of records and average sleep hours, coffee intake and BMI for each group.

    Takes the same filters as the list. Records with no value to group by form a group whose
    key is null, so the counts add up to the number of records that match the filters. This is
    where to get a total: the list does not return one.
    """
    where, values = build_where(params)
    query = _SUMMARIZE_RECORDS.format(key=GROUP_BY[params.group_by], filters=_where(where, values))
    return [SummaryRow.model_validate(row) for row in conn.execute(query, values).fetchall()]


# --- rows that were not loaded -----------------------------------------------------------

_REJECT_COLUMNS = sql.SQL(", ").join(sql.Identifier(name) for name in Reject.model_fields)

_LIST_REJECTS = sql.SQL(
    "select {columns} from load_rejects{filters} order by source_file, line_number limit %s"
)

_COUNT_REJECTS = sql.SQL(
    "select reason, count(*) as count from load_rejects{filters}"
    " group by reason order by count(*) desc, reason"
)


@router.get("/rejects", summary="List the source lines that were not loaded", tags=["rejects"])
def list_rejects(params: Annotated[RejectListParams, Query()], conn: Conn) -> RejectPage:
    """Every line of a loaded file that did not become a record, with the reason.

    Ordered by file and line number. To read the next page, repeat the request with
    `after_source_file` and `after_line_number` set to the two values of `next_after`.
    """
    after = None
    if params.after_source_file is not None and params.after_line_number is not None:
        after = (params.after_source_file, params.after_line_number)
    where, values = build_reject_where(
        after=after, reasons=params.reason, source_file=params.source_file
    )
    query = _LIST_REJECTS.format(columns=_REJECT_COLUMNS, filters=_where(where, values))
    rows = conn.execute(query, [*values, params.limit + 1]).fetchall()
    page, last = _page(rows, params.limit)
    return RejectPage(
        items=[Reject.model_validate(row) for row in page],
        next_after=RejectCursor.model_validate(last) if last else None,
        limit=params.limit,
    )


@router.get("/rejects/summary", summary="Count the lines not loaded, by reason", tags=["rejects"])
def count_rejects(params: Annotated[RejectSummaryParams, Query()], conn: Conn) -> list[ReasonCount]:
    """How many source lines were not loaded for each reason, most frequent first."""
    where, values = build_reject_where(source_file=params.source_file)
    query = _COUNT_REJECTS.format(filters=_where(where, values))
    return [ReasonCount.model_validate(row) for row in conn.execute(query, values).fetchall()]
