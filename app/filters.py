"""Query parameters of the read API and the SQL conditions they turn into.

Everything here is pure: no connection, no I/O. Each route takes one of these models as its
query string, so the rules are declared once, tested without HTTP or a database, and shown in
the interactive documentation. Unknown parameters are rejected instead of silently ignored.
"""

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, LiteralString

from psycopg import sql
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.validation import COUNTRIES, Gender, RejectReason, SleepQuality, StressLevel

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# --- normalization of multi-value filters ------------------------------------------------
#
# Same rule as the loader: trim, then match ignoring case. The database only holds canonical
# values, so after this the query can compare with plain equality and use an ordinary index.

_COUNTRY_BY_FOLDED = {country.casefold(): country for country in COUNTRIES}


def _canonical_countries(values: object) -> object:
    """Known countries in their canonical spelling; unknown ones trimmed and kept.

    An unknown country is not an error: it matches no record and the page comes back empty.
    """
    if not isinstance(values, list):
        return values
    return [
        _COUNTRY_BY_FOLDED.get(value.strip().casefold(), value.strip())
        if isinstance(value, str)
        else value
        for value in values
    ]


def _canonical[E: StrEnum](enum: type[E]) -> BeforeValidator:
    """Catalog values matched ignoring case and surrounding whitespace.

    Anything outside the catalog is passed through, so Pydantic rejects it with its standard
    enum error, which lists the accepted values.
    """
    by_folded = {member.value.casefold(): member for member in enum}

    def normalize(values: object) -> object:
        if not isinstance(values, list):
            return values
        return [
            by_folded.get(value.strip().casefold(), value) if isinstance(value, str) else value
            for value in values
        ]

    return BeforeValidator(normalize)


# --- parameters --------------------------------------------------------------------------

_ANY_OF_SEVERAL = "Repeat the parameter to match any of several values. Case is ignored."


class RecordFilters(BaseModel):
    """Filters shared by the record list and the summary. All optional, combined with AND.

    A filter on a column never matches a record that has no value in that column.
    Each min_* is declared before its max_*: the range check reads the minimum first.
    """

    model_config = ConfigDict(extra="forbid")

    country: Annotated[
        # The example is what /docs pre-fills when a value is added, instead of "string".
        list[Annotated[str, Field(examples=["Germany"])]] | None,
        BeforeValidator(_canonical_countries),
        Field(
            description="Country name. Repeat the parameter to match any of several. Case and"
            " surrounding spaces are ignored. An unknown country matches nothing."
        ),
    ] = None
    gender: Annotated[
        list[Gender] | None, _canonical(Gender), Field(description=_ANY_OF_SEVERAL)
    ] = None
    sleep_quality: Annotated[
        list[SleepQuality] | None, _canonical(SleepQuality), Field(description=_ANY_OF_SEVERAL)
    ] = None
    stress_level: Annotated[
        list[StressLevel] | None, _canonical(StressLevel), Field(description=_ANY_OF_SEVERAL)
    ] = None
    smoking: bool | None = Field(
        default=None, description="true for smokers, false for non-smokers."
    )
    min_age: int | None = Field(default=None, description="Lowest age, inclusive.")
    max_age: int | None = Field(default=None, description="Highest age, inclusive.")
    min_coffee_intake: Decimal | None = Field(
        default=None, description="Fewest cups of coffee per day, inclusive."
    )
    max_coffee_intake: Decimal | None = Field(
        default=None, description="Most cups of coffee per day, inclusive."
    )
    min_bmi: Decimal | None = Field(default=None, description="Lowest BMI, inclusive.")
    max_bmi: Decimal | None = Field(default=None, description="Highest BMI, inclusive.")

    @field_validator("max_age", "max_coffee_intake", "max_bmi")
    @classmethod
    def _not_below_its_minimum(
        cls, maximum: Decimal | int | None, info: ValidationInfo
    ) -> Decimal | int | None:
        """Reject max < min, on the max_* parameter and together with any other error.

        A field validator, not a model validator: the latter does not run once another
        parameter has failed, so the client would learn about this error only after fixing
        the rest. `info.data` holds the fields validated so far; an invalid minimum is absent
        from it and then there is nothing to compare.
        """
        max_name = str(info.field_name)
        min_name = "min_" + max_name.removeprefix("max_")
        minimum: Decimal | int | None = info.data.get(min_name)
        if maximum is not None and minimum is not None and Decimal(maximum) < Decimal(minimum):
            raise PydanticCustomError(
                "range_inverted",
                "{max_name} must be greater than or equal to {min_name}",
                {"max_name": max_name, "min_name": min_name},
            )
        return maximum


class RecordListParams(RecordFilters):
    """Query string of GET /records."""

    limit: int = Field(
        default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Records per page."
    )
    after_id: int = Field(
        default=0,
        ge=0,
        description="Cursor: only records with a greater ID are returned. Send the"
        " next_after_id of the previous page.",
    )


class GroupBy(StrEnum):
    COUNTRY = "country"
    GENDER = "gender"
    SLEEP_QUALITY = "sleep_quality"
    STRESS_LEVEL = "stress_level"
    COFFEE_CUPS = "coffee_cups"


# What each grouping means in SQL. A fixed mapping: the client picks a key, never the SQL.
# floor() gives one-cup buckets: group n holds n <= coffee_intake < n + 1.
GROUP_BY: dict[GroupBy, sql.Composable] = {
    GroupBy.COUNTRY: sql.Identifier("country"),
    GroupBy.GENDER: sql.Identifier("gender"),
    GroupBy.SLEEP_QUALITY: sql.Identifier("sleep_quality"),
    GroupBy.STRESS_LEVEL: sql.Identifier("stress_level"),
    GroupBy.COFFEE_CUPS: sql.SQL("floor(coffee_intake)::int"),
}


class SummaryParams(RecordFilters):
    """Query string of GET /records/summary: the list filters plus a required grouping."""

    group_by: GroupBy = Field(
        description="What to group by. coffee_cups groups by whole cups: group n holds the"
        " records with n <= coffee_intake < n + 1."
    )


class RejectListParams(BaseModel):
    """Query string of GET /rejects.

    The cursor has two parts because the rows are ordered by (source_file, line_number).
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Rows per page."
    )
    after_source_file: str | None = Field(
        default=None,
        description="Cursor, first part: the source_file of the previous page's next_after."
        " Send both parts or neither.",
    )
    # validate_default: the check below must also run when this parameter is absent.
    after_line_number: int | None = Field(
        default=None,
        ge=0,
        validate_default=True,
        description="Cursor, second part: the line_number of the previous page's next_after.",
    )
    reason: Annotated[
        list[RejectReason] | None,
        _canonical(RejectReason),
        Field(description="Why the line was not loaded. " + _ANY_OF_SEVERAL),
    ] = None
    source_file: str | None = Field(
        default=None, description="Exact name of the loaded file, e.g. coffee_health_raw.csv."
    )

    @field_validator("after_line_number")
    @classmethod
    def _cursor_is_complete(cls, line_number: int | None, info: ValidationInfo) -> int | None:
        """Both parts of the cursor or neither. Always reported on this parameter: a field
        validator only sees the fields declared before it, so it lives on the second one."""
        if (line_number is None) != (info.data.get("after_source_file") is None):
            raise PydanticCustomError(
                "cursor_incomplete",
                "after_source_file and after_line_number must be given together",
            )
        return line_number


class RejectSummaryParams(BaseModel):
    """Query string of GET /rejects/summary."""

    model_config = ConfigDict(extra="forbid")

    source_file: str | None = Field(
        default=None, description="Count only the lines of this file (exact name)."
    )


# --- SQL conditions ----------------------------------------------------------------------


def build_where(filters: RecordFilters) -> tuple[sql.Composable, list[object]]:
    """The filters as SQL conditions joined with AND, and their parameters in the same order.

    Returns empty SQL when no filter is set. Column names come from this function, never from
    the client; client values only travel as query parameters.
    """
    conditions: list[sql.Composable] = []
    params: list[object] = []

    def add(template: LiteralString, column: str, value: object) -> None:
        if value is not None:
            conditions.append(sql.SQL(template).format(sql.Identifier(column)))
            params.append(value)

    add("{} = any(%s)", "country", filters.country)
    add("{} = any(%s)", "gender", filters.gender)
    add("{} = any(%s)", "sleep_quality", filters.sleep_quality)
    add("{} = any(%s)", "stress_level", filters.stress_level)
    add("{} = %s", "smoking", filters.smoking)
    add("{} >= %s", "age", filters.min_age)
    add("{} <= %s", "age", filters.max_age)
    add("{} >= %s", "coffee_intake", filters.min_coffee_intake)
    add("{} <= %s", "coffee_intake", filters.max_coffee_intake)
    add("{} >= %s", "bmi", filters.min_bmi)
    add("{} <= %s", "bmi", filters.max_bmi)
    return sql.SQL(" and ").join(conditions), params


def build_reject_where(
    *,
    after: tuple[str, int] | None = None,
    reasons: Sequence[RejectReason] | None = None,
    source_file: str | None = None,
) -> tuple[sql.Composable, list[object]]:
    """Conditions over load_rejects joined with AND, and their parameters in the same order.

    `after` is the cursor: the row comparison follows the primary key (source_file,
    line_number), so it is an index range scan like `id > %s` on the record list.
    """
    conditions: list[sql.Composable] = []
    params: list[object] = []
    if after is not None:
        conditions.append(sql.SQL('("source_file", "line_number") > (%s, %s)'))
        params += after
    if reasons is not None:
        conditions.append(sql.SQL('"reason" = any(%s)'))
        params.append(list(reasons))
    if source_file is not None:
        conditions.append(sql.SQL('"source_file" = %s'))
        params.append(source_file)
    return sql.SQL(" and ").join(conditions), params
