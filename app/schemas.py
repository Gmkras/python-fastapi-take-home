"""Response models of the read API.

Numeric columns are declared as float on purpose: Pydantic serializes Decimal as a string and
clients expect JSON numbers. With one decimal place nothing visible is lost.
"""

from pydantic import BaseModel

from app.validation import (
    Gender,
    HealthIssues,
    Occupation,
    RejectReason,
    SleepQuality,
    StressLevel,
)


class Record(BaseModel):
    """One person, as stored. Only id, country, coffee_intake and caffeine_mg are always set."""

    id: int
    age: int | None
    gender: Gender | None
    country: str
    coffee_intake: float
    caffeine_mg: float
    sleep_hours: float | None
    sleep_quality: SleepQuality | None
    bmi: float | None
    heart_rate: int | None
    stress_level: StressLevel | None
    physical_activity_hours: float | None
    health_issues: HealthIssues | None
    occupation: Occupation | None
    smoking: bool | None
    alcohol_consumption: bool | None


class RecordPage(BaseModel):
    """One page of records. `next_after_id` is null on the last page."""

    items: list[Record]
    next_after_id: int | None
    limit: int


class SummaryRow(BaseModel):
    """One group of the summary.

    `key` is text for a country or a category, a whole number of cups for coffee_cups, and
    null for the records that have no value to group by. An average is null when no record of
    the group has that value.
    """

    key: str | int | None
    count: int
    avg_sleep_hours: float | None
    avg_coffee_intake: float | None
    avg_bmi: float | None


class Reject(BaseModel):
    """A source line that was not loaded, and why.

    `column_name` and `raw_value` are null when the reason is not about one column (wrong
    number of fields, duplicates). For a duplicate, `detail` names the line that replaced it.
    """

    source_file: str
    line_number: int
    reason: RejectReason
    column_name: str | None
    raw_value: str | None
    detail: str


class RejectCursor(BaseModel):
    source_file: str
    line_number: int


class RejectPage(BaseModel):
    """One page of rejects. `next_after` is null on the last page."""

    items: list[Reject]
    next_after: RejectCursor | None
    limit: int


class ReasonCount(BaseModel):
    reason: RejectReason
    count: int
