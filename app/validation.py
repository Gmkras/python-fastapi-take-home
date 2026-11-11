"""Parsing, validation and normalization rules for the coffee health dataset.

Everything here is pure: no files, no network, no database. The loader calls `parse_row` for
every CSV line and gets back either a `ParsedRow` (a normalized record plus what was fixed or
missing) or a `Rejection` (why the line cannot be loaded).

Parsing is strict on purpose. Pydantic's default coercion would accept things a dirty export
must not get away with ("on" as a boolean, "1e3" or "NaN" as a decimal), so every field goes
through its own before-validator and Pydantic only enforces the declared ranges and types.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any, NamedTuple

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, ValidationInfo
from pydantic_core import PydanticCustomError

# --- catalogs and limits (single source of truth) ----------------------------------------

EXPECTED_HEADER: tuple[str, ...] = (
    "ID",
    "Age",
    "Gender",
    "Country",
    "Coffee_Intake",
    "Caffeine_mg",
    "Sleep_Hours",
    "Sleep_Quality",
    "BMI",
    "Heart_Rate",
    "Stress_Level",
    "Physical_Activity_Hours",
    "Health_Issues",
    "Occupation",
    "Smoking",
    "Alcohol_Consumption",
)

# Record field (and database column) names, in the same order as EXPECTED_HEADER.
FIELD_NAMES: tuple[str, ...] = tuple(name.lower() for name in EXPECTED_HEADER)

COUNTRIES: frozenset[str] = frozenset(
    {
        "Australia",
        "Belgium",
        "Brazil",
        "Canada",
        "China",
        "Finland",
        "France",
        "Germany",
        "India",
        "Italy",
        "Japan",
        "Mexico",
        "Netherlands",
        "Norway",
        "South Korea",
        "Spain",
        "Sweden",
        "Switzerland",
        "UK",
        "USA",
    }
)

# Conventional ways of saying "not known". Compared after strip() and casefold().
# "none" is deliberately absent: `None` is a real Health_Issues value.
MISSING_MARKERS: frozenset[str] = frozenset({"", "n/a", "na", "?", "unknown", "null"})

TRUE_TOKENS: frozenset[str] = frozenset({"1", "y", "yes", "true"})
FALSE_TOKENS: frozenset[str] = frozenset({"0", "n", "no", "false"})

# Inclusive plausible ranges. Deliberately looser than the observed data: they only exclude
# what is physically impossible or clearly a capture error.
ID_RANGE = (1, 2_147_483_647)  # upper bound = PostgreSQL integer
AGE_RANGE = (18, 120)  # the dataset is about adults
COFFEE_INTAKE_RANGE = (0, 20)  # cups per day
CAFFEINE_MG_RANGE = (0, 2000)  # mg per day
SLEEP_HOURS_RANGE = (1, 16)  # hours per night
BMI_RANGE = (10, 80)
HEART_RATE_RANGE = (30, 200)  # resting bpm
PHYSICAL_ACTIVITY_HOURS_RANGE = (0, 80)  # hours per week

_ONE_DECIMAL = Decimal("0.1")
# [0-9], not \d: in a str pattern \d also matches non-ASCII digits such as "４０".
_INTEGER = re.compile(r"[0-9]+")
_DECIMAL = re.compile(r"[0-9]+(?:\.([0-9]+))?")  # group 1: the fraction digits
_MAX_NUMBER_LENGTH = 20


class Gender(StrEnum):
    MALE = "Male"
    FEMALE = "Female"
    OTHER = "Other"


class SleepQuality(StrEnum):
    POOR = "Poor"
    FAIR = "Fair"
    GOOD = "Good"
    EXCELLENT = "Excellent"


class StressLevel(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class HealthIssues(StrEnum):
    NONE = "None"
    MILD = "Mild"
    MODERATE = "Moderate"
    SEVERE = "Severe"


class Occupation(StrEnum):
    OFFICE = "Office"
    HEALTHCARE = "Healthcare"
    SERVICE = "Service"
    STUDENT = "Student"
    OTHER = "Other"


class RejectReason(StrEnum):
    """Why a source line was not loaded. Mirrored by a CHECK on load_rejects.reason."""

    COLUMN_COUNT = "column_count"
    MISSING_REQUIRED = "missing_required"
    NOT_NUMERIC = "not_numeric"
    OUT_OF_RANGE = "out_of_range"
    UNKNOWN_CATEGORY = "unknown_category"
    UNKNOWN_COUNTRY = "unknown_country"
    INVALID_BOOLEAN = "invalid_boolean"
    DUPLICATE_EXACT = "duplicate_exact"
    DUPLICATE_CONFLICT = "duplicate_conflict"


# --- field parsers -----------------------------------------------------------------------
#
# One before-validator per kind of field. Each tries the common case first (a clean, valid
# value) and only when that fails asks whether the value is a missing marker or an error.
# The loader runs these millions of times, almost always on clean values.


def _note(info: ValidationInfo, kind: str) -> None:
    """Record in the validation context that this field was fixed or is missing."""
    if info.context is not None and info.field_name is not None:
        info.context[kind].add(info.field_name)


def _unparsed(
    text: str, info: ValidationInfo, required: bool, reason: RejectReason, message: str
) -> object:
    """Handle a value that did not parse: a missing marker (the field is None) or an error."""
    if text.casefold() not in MISSING_MARKERS:
        raise PydanticCustomError(reason.value, message)
    if required:
        raise PydanticCustomError(RejectReason.MISSING_REQUIRED.value, "required value is missing")
    _note(info, "missing")
    return None


def _too_long(text: str) -> bool:
    """No plausible value has this many digits; skip converting it at all."""
    return len(text) > _MAX_NUMBER_LENGTH


def _int_field(*, required: bool = False) -> BeforeValidator:
    def validate(value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value  # already typed (not from a CSV); let Pydantic check it
        text = value.strip()
        if not _INTEGER.fullmatch(text):
            return _unparsed(text, info, required, RejectReason.NOT_NUMERIC, "not a whole number")
        if _too_long(text):
            raise PydanticCustomError(RejectReason.OUT_OF_RANGE.value, "number is out of range")
        if text != value:
            _note(info, "fixed")
        return int(text)

    return BeforeValidator(validate)


def _decimal_field(*, required: bool = False) -> BeforeValidator:
    def validate(value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip()
        match = _DECIMAL.fullmatch(text)
        if match is None:
            return _unparsed(text, info, required, RejectReason.NOT_NUMERIC, "not a decimal number")
        if _too_long(text):
            raise PydanticCustomError(RejectReason.OUT_OF_RANGE.value, "number is out of range")
        if text != value:
            _note(info, "fixed")
        number = Decimal(text)
        fraction = match.group(1)
        if fraction is None or len(fraction) == 1:
            return number
        rounded = number.quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)
        if rounded != number:
            _note(info, "fixed")
        return rounded

    return BeforeValidator(validate)


def _bool_field() -> BeforeValidator:
    def validate(value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        if value == "0":
            return False
        if value == "1":
            return True
        text = value.strip()
        token = text.casefold()
        if token not in TRUE_TOKENS and token not in FALSE_TOKENS:
            return _unparsed(text, info, False, RejectReason.INVALID_BOOLEAN, "not a boolean")
        _note(info, "fixed")  # padded, or a synonym such as "Y" or "false"
        return token in TRUE_TOKENS

    return BeforeValidator(validate)


def _catalog_field[T: str](
    values: Iterable[T], reason: RejectReason, *, required: bool = False
) -> BeforeValidator:
    """A value from a closed catalog, matched ignoring case and surrounding whitespace."""
    canonical = {str(value): value for value in values}
    folded = {text.casefold(): value for text, value in canonical.items()}

    def validate(value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        exact = canonical.get(value)
        if exact is not None:
            return exact
        text = value.strip()
        match = folded.get(text.casefold())
        if match is None:
            return _unparsed(text, info, required, reason, "value is not in the catalog")
        _note(info, "fixed")  # same value, different case or padding
        return match

    return BeforeValidator(validate)


_REQUIRED_INT = _int_field(required=True)
_OPTIONAL_INT = _int_field()
_REQUIRED_DECIMAL = _decimal_field(required=True)
_OPTIONAL_DECIMAL = _decimal_field()
_OPTIONAL_BOOL = _bool_field()
_REQUIRED_COUNTRY = _catalog_field(COUNTRIES, RejectReason.UNKNOWN_COUNTRY, required=True)


def _category(enum: type[StrEnum]) -> BeforeValidator:
    return _catalog_field(enum, RejectReason.UNKNOWN_CATEGORY)


# --- the record --------------------------------------------------------------------------


class HealthRecord(BaseModel):
    """One validated, normalized person record.

    Only id, country, coffee_intake and caffeine_mg are required; the rest may be missing in
    the source and are then None. Range constraints sit inside the optional type on purpose:
    applied to the union they raise TypeError on None instead of a validation error.
    """

    model_config = ConfigDict(frozen=True)

    id: Annotated[int, Field(ge=ID_RANGE[0], le=ID_RANGE[1]), _REQUIRED_INT]
    age: Annotated[Annotated[int, Field(ge=AGE_RANGE[0], le=AGE_RANGE[1])] | None, _OPTIONAL_INT]
    gender: Annotated[Gender | None, _category(Gender)]
    country: Annotated[str, _REQUIRED_COUNTRY]
    coffee_intake: Annotated[
        Decimal, Field(ge=COFFEE_INTAKE_RANGE[0], le=COFFEE_INTAKE_RANGE[1]), _REQUIRED_DECIMAL
    ]
    caffeine_mg: Annotated[
        Decimal, Field(ge=CAFFEINE_MG_RANGE[0], le=CAFFEINE_MG_RANGE[1]), _REQUIRED_DECIMAL
    ]
    sleep_hours: Annotated[
        Annotated[Decimal, Field(ge=SLEEP_HOURS_RANGE[0], le=SLEEP_HOURS_RANGE[1])] | None,
        _OPTIONAL_DECIMAL,
    ]
    sleep_quality: Annotated[SleepQuality | None, _category(SleepQuality)]
    bmi: Annotated[
        Annotated[Decimal, Field(ge=BMI_RANGE[0], le=BMI_RANGE[1])] | None, _OPTIONAL_DECIMAL
    ]
    heart_rate: Annotated[
        Annotated[int, Field(ge=HEART_RATE_RANGE[0], le=HEART_RATE_RANGE[1])] | None,
        _OPTIONAL_INT,
    ]
    stress_level: Annotated[StressLevel | None, _category(StressLevel)]
    physical_activity_hours: Annotated[
        Annotated[
            Decimal,
            Field(ge=PHYSICAL_ACTIVITY_HOURS_RANGE[0], le=PHYSICAL_ACTIVITY_HOURS_RANGE[1]),
        ]
        | None,
        _OPTIONAL_DECIMAL,
    ]
    health_issues: Annotated[HealthIssues | None, _category(HealthIssues)]
    occupation: Annotated[Occupation | None, _category(Occupation)]
    smoking: Annotated[bool | None, _OPTIONAL_BOOL]
    alcohol_consumption: Annotated[bool | None, _OPTIONAL_BOOL]


# --- row-level results -------------------------------------------------------------------


class ParsedRow(NamedTuple):
    """A valid line: the record, plus which fields were normalized or are missing.

    A NamedTuple rather than a dataclass: one is built per valid row, and it is much cheaper.
    """

    line_number: int
    record: HealthRecord
    fixed: frozenset[str]
    missing: frozenset[str]


@dataclass(frozen=True)
class Rejection:
    """A line that cannot be loaded, with enough detail to explain it later."""

    line_number: int
    reason: RejectReason
    column_name: str | None
    raw_value: str | None
    detail: str
    raw_fields: tuple[str, ...]


class HeaderError(ValueError):
    """The CSV header is not the expected one; nothing can be loaded safely."""


_NOTHING: frozenset[str] = frozenset()
_RANGE_ERROR_TYPES = frozenset({"greater_than_equal", "less_than_equal"})


def check_header(fields: Sequence[str]) -> None:
    """Raise HeaderError unless `fields` is exactly the expected header, in order."""
    if tuple(fields) != EXPECTED_HEADER:
        raise HeaderError(
            f"unexpected header: expected {list(EXPECTED_HEADER)!r}, found {list(fields)!r}"
        )


def parse_row(line_number: int, fields: Sequence[str]) -> ParsedRow | Rejection:
    """Validate and normalize one CSV data line. Never raises for bad data."""
    raw_fields = tuple(fields)
    if len(raw_fields) != len(EXPECTED_HEADER):
        return Rejection(
            line_number=line_number,
            reason=RejectReason.COLUMN_COUNT,
            column_name=None,
            raw_value=None,
            detail=f"expected {len(EXPECTED_HEADER)} fields, found {len(raw_fields)}",
            raw_fields=raw_fields,
        )

    context: dict[str, set[str]] = {"fixed": set(), "missing": set()}
    try:
        record = HealthRecord.model_validate(
            dict(zip(FIELD_NAMES, raw_fields, strict=True)), context=context
        )
    except ValidationError as error:
        return _rejection(line_number, raw_fields, error)
    return ParsedRow(
        line_number=line_number,
        record=record,
        fixed=frozenset(context["fixed"]) if context["fixed"] else _NOTHING,
        missing=frozenset(context["missing"]) if context["missing"] else _NOTHING,
    )


def _rejection(line_number: int, raw_fields: tuple[str, ...], error: ValidationError) -> Rejection:
    """Turn Pydantic's per-field errors into one Rejection.

    The main reason is the first invalid column in header order; `detail` names all of them.
    Values are reported as they appear in the file, untrimmed.
    """
    problems: list[tuple[int, RejectReason]] = []
    for item in error.errors():
        index = FIELD_NAMES.index(str(item["loc"][0]))
        problems.append((index, _reason(item)))
    problems.sort()

    first_index, first_reason = problems[0]
    return Rejection(
        line_number=line_number,
        reason=first_reason,
        column_name=EXPECTED_HEADER[first_index],
        raw_value=raw_fields[first_index],
        detail="; ".join(
            f"{EXPECTED_HEADER[index]}={raw_fields[index]!r}: {reason.value}"
            for index, reason in problems
        ),
        raw_fields=raw_fields,
    )


def _reason(error: Any) -> RejectReason:
    error_type = str(error["type"])
    if error_type in _RANGE_ERROR_TYPES:
        return RejectReason.OUT_OF_RANGE
    return RejectReason(error_type)  # the parsers above only raise RejectReason codes
