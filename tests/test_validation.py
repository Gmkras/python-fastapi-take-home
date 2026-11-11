"""Validation and normalization rules. These tests never touch the database."""

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from app.validation import (
    EXPECTED_HEADER,
    Gender,
    HeaderError,
    HealthIssues,
    Occupation,
    ParsedRow,
    Rejection,
    RejectReason,
    SleepQuality,
    StressLevel,
    check_header,
    parse_row,
)

VALID: dict[str, str] = {
    "ID": "1",
    "Age": "40",
    "Gender": "Male",
    "Country": "Germany",
    "Coffee_Intake": "3.5",
    "Caffeine_mg": "328.1",
    "Sleep_Hours": "7.5",
    "Sleep_Quality": "Good",
    "BMI": "24.9",
    "Heart_Rate": "78",
    "Stress_Level": "Low",
    "Physical_Activity_Hours": "14.5",
    "Health_Issues": "None",
    "Occupation": "Other",
    "Smoking": "0",
    "Alcohol_Consumption": "0",
}

OPTIONAL_COLUMNS = ["Age", "Gender", "Sleep_Hours", "BMI", "Heart_Rate"]
REQUIRED_COLUMNS = ["ID", "Country", "Coffee_Intake", "Caffeine_mg"]
MISSING_VALUES = ["", "n/a", "N/A", "?", "unknown", "  "]


def row(**overrides: str) -> list[str]:
    values = VALID | overrides
    return [values[column] for column in EXPECTED_HEADER]


def accept(**overrides: str) -> ParsedRow:
    result = parse_row(2, row(**overrides))
    assert isinstance(result, ParsedRow), result
    return result


def reject(**overrides: str) -> Rejection:
    result = parse_row(2, row(**overrides))
    assert isinstance(result, Rejection), result
    return result


# --- whole row ---------------------------------------------------------------------------


def test_clean_row_is_accepted_without_fixes() -> None:
    parsed = accept()

    assert parsed.line_number == 2
    assert parsed.fixed == frozenset()
    assert parsed.missing == frozenset()
    record = parsed.record
    assert record.id == 1
    assert record.age == 40
    assert record.gender is Gender.MALE
    assert record.country == "Germany"
    assert record.coffee_intake == Decimal("3.5")
    assert record.health_issues is HealthIssues.NONE
    assert record.smoking is False


def test_parse_row_does_not_modify_its_input() -> None:
    fields = row(Country="  germany ")
    before = list(fields)

    parse_row(2, fields)

    assert fields == before


# --- integers ----------------------------------------------------------------------------


def test_integer_with_surrounding_whitespace_is_fixed() -> None:
    parsed = accept(Age=" 40 ")

    assert parsed.record.age == 40
    assert parsed.fixed == {"age"}


@pytest.mark.parametrize(
    "value", ["4O", "thirty", "-3", "+40", "40.0", "4_0", "1e2", "４０", "٤٠", "4 0"]
)
def test_integer_that_is_not_plain_digits_is_rejected(value: str) -> None:
    rejection = reject(Age=value)

    assert rejection.reason is RejectReason.NOT_NUMERIC
    assert rejection.column_name == "Age"
    assert rejection.raw_value == value


@pytest.mark.parametrize(
    ("column", "value", "valid"),
    [
        ("ID", "0", False),
        ("ID", "1", True),
        ("ID", "2147483647", True),
        ("ID", "2147483648", False),
        ("Age", "17", False),
        ("Age", "18", True),
        ("Age", "120", True),
        ("Age", "121", False),
        ("Age", "150", False),
        ("Heart_Rate", "0", False),
        ("Heart_Rate", "29", False),
        ("Heart_Rate", "30", True),
        ("Heart_Rate", "200", True),
        ("Heart_Rate", "201", False),
        ("Heart_Rate", "350", False),
        ("Sleep_Hours", "0.9", False),
        ("Sleep_Hours", "1.0", True),
        ("Sleep_Hours", "16.0", True),
        ("Sleep_Hours", "16.1", False),
        ("Sleep_Hours", "25.0", False),
        ("BMI", "0", False),
        ("BMI", "9.9", False),
        ("BMI", "10.0", True),
        ("BMI", "80.0", True),
        ("BMI", "80.1", False),
        ("Coffee_Intake", "0", True),
        ("Coffee_Intake", "20.0", True),
        ("Coffee_Intake", "20.1", False),
        ("Caffeine_mg", "0.0", True),
        ("Caffeine_mg", "2000.0", True),
        ("Caffeine_mg", "2000.1", False),
        ("Physical_Activity_Hours", "0", True),
        ("Physical_Activity_Hours", "80.0", True),
        ("Physical_Activity_Hours", "80.1", False),
    ],
)
def test_numeric_ranges_are_inclusive(column: str, value: str, valid: bool) -> None:
    result = parse_row(2, row(**{column: value}))

    if valid:
        assert isinstance(result, ParsedRow), result
    else:
        assert isinstance(result, Rejection), result
        assert result.reason is RejectReason.OUT_OF_RANGE
        assert result.column_name == column
        assert result.raw_value == value


# --- decimals ----------------------------------------------------------------------------


def test_decimal_is_parsed_as_decimal_never_float() -> None:
    parsed = accept(Coffee_Intake="3.5")

    assert isinstance(parsed.record.coffee_intake, Decimal)
    assert parsed.record.coffee_intake == Decimal("3.5")
    assert parsed.fixed == frozenset()


def test_decimal_without_fraction_is_valid_and_not_a_fix() -> None:
    parsed = accept(BMI="24")

    assert parsed.record.bmi == Decimal("24.0")
    assert parsed.fixed == frozenset()


@pytest.mark.parametrize(("value", "expected"), [("3.14", "3.1"), ("3.15", "3.2"), ("3.96", "4.0")])
def test_decimal_with_extra_places_is_rounded_half_up_and_fixed(value: str, expected: str) -> None:
    parsed = accept(Coffee_Intake=value)

    assert parsed.record.coffee_intake == Decimal(expected)
    assert parsed.fixed == {"coffee_intake"}


def test_decimal_is_range_checked_after_rounding() -> None:
    assert accept(BMI="9.96").record.bmi == Decimal("10.0")
    assert reject(BMI="80.05").reason is RejectReason.OUT_OF_RANGE


@pytest.mark.parametrize(
    "value",
    ["3,5", ".5", "5.", "-1", "+1.5", "NaN", "Infinity", "1e3", "seven", "1_0.5", "３.５"],
)
def test_decimal_that_is_not_plain_digits_is_rejected(value: str) -> None:
    rejection = reject(Sleep_Hours=value)

    assert rejection.reason is RejectReason.NOT_NUMERIC
    assert rejection.column_name == "Sleep_Hours"


def test_absurdly_long_number_is_rejected_not_crashed() -> None:
    assert reject(Caffeine_mg="9" * 60).reason is RejectReason.OUT_OF_RANGE
    assert reject(Age="9" * 60).reason is RejectReason.OUT_OF_RANGE
    assert reject(Age="9" * 5000).reason is RejectReason.OUT_OF_RANGE
    assert reject(BMI="1." + "9" * 5000).reason is RejectReason.OUT_OF_RANGE


# --- categories --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value", "field", "expected"),
    [
        ("Gender", "  Female  ", "gender", Gender.FEMALE),
        ("Gender", "\tMale  ", "gender", Gender.MALE),
        ("Gender", "MALE", "gender", Gender.MALE),
        ("Gender", "male", "gender", Gender.MALE),
        ("Sleep_Quality", "GOOD", "sleep_quality", SleepQuality.GOOD),
        ("Sleep_Quality", " Good ", "sleep_quality", SleepQuality.GOOD),
        ("Stress_Level", "low", "stress_level", StressLevel.LOW),
        ("Occupation", " Office ", "occupation", Occupation.OFFICE),
        ("Health_Issues", "none", "health_issues", HealthIssues.NONE),
    ],
)
def test_category_is_normalized_to_canonical_form(
    column: str, value: str, field: str, expected: object
) -> None:
    parsed = accept(**{column: value})

    assert getattr(parsed.record, field) is expected
    assert parsed.fixed == {field}


@pytest.mark.parametrize(
    ("column", "enum"),
    [
        ("Gender", Gender),
        ("Sleep_Quality", SleepQuality),
        ("Stress_Level", StressLevel),
        ("Health_Issues", HealthIssues),
        ("Occupation", Occupation),
    ],
)
def test_every_canonical_category_value_is_accepted_as_is(
    column: str, enum: type[Gender | SleepQuality | StressLevel | HealthIssues | Occupation]
) -> None:
    for member in enum:
        assert accept(**{column: member.value}).fixed == frozenset()


def test_health_issues_none_is_a_value_not_a_missing_marker() -> None:
    parsed = accept(Health_Issues="None")

    assert parsed.record.health_issues is HealthIssues.NONE
    assert parsed.missing == frozenset()


@pytest.mark.parametrize(("column", "value"), [("Gender", "X"), ("Sleep_Quality", "Great")])
def test_unknown_category_is_rejected(column: str, value: str) -> None:
    rejection = reject(**{column: value})

    assert rejection.reason is RejectReason.UNKNOWN_CATEGORY
    assert rejection.column_name == column


# --- countries ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("usa", "USA"),
        (" USA", "USA"),
        ("\tUSA ", "USA"),
        ("uk", "UK"),
        ("south korea", "South Korea"),
        ("  South Korea  ", "South Korea"),
        ("SWEDEN", "Sweden"),
        ("belgium", "Belgium"),
        ("\tBrazil  ", "Brazil"),
    ],
)
def test_country_is_normalized_without_breaking_acronyms(value: str, expected: str) -> None:
    parsed = accept(Country=value)

    assert parsed.record.country == expected
    assert parsed.fixed == {"country"}


def test_canonical_country_is_not_a_fix() -> None:
    assert accept(Country="USA").fixed == frozenset()


def test_unknown_country_is_rejected() -> None:
    rejection = reject(Country="Atlantis")

    assert rejection.reason is RejectReason.UNKNOWN_COUNTRY
    assert rejection.column_name == "Country"


# --- booleans ----------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [("0", False), ("1", True)])
def test_zero_and_one_are_booleans_without_fix(value: str, expected: bool) -> None:
    parsed = accept(Smoking=value)

    assert parsed.record.smoking is expected
    assert parsed.fixed == frozenset()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Y", True),
        ("y", True),
        ("yes", True),
        ("YES", True),
        ("true", True),
        ("N", False),
        ("no", False),
        ("false", False),
        ("False", False),
    ],
)
def test_unambiguous_boolean_synonyms_are_fixed(value: str, expected: bool) -> None:
    parsed = accept(Alcohol_Consumption=value)

    assert parsed.record.alcohol_consumption is expected
    assert parsed.fixed == {"alcohol_consumption"}


@pytest.mark.parametrize("value", ["-1", "2", "maybe", "on", "off", "t", "f", "10"])
def test_boolean_outside_the_domain_is_rejected(value: str) -> None:
    rejection = reject(Smoking=value)

    assert rejection.reason is RejectReason.INVALID_BOOLEAN
    assert rejection.column_name == "Smoking"


# --- missing values ----------------------------------------------------------------------


@pytest.mark.parametrize("column", OPTIONAL_COLUMNS)
@pytest.mark.parametrize("value", MISSING_VALUES)
def test_missing_optional_value_is_loaded_as_none(column: str, value: str) -> None:
    parsed = accept(**{column: value})

    field = column.lower()
    assert getattr(parsed.record, field) is None
    assert parsed.missing == {field}
    assert parsed.fixed == frozenset()


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
@pytest.mark.parametrize("value", MISSING_VALUES)
def test_missing_required_value_rejects_the_row(column: str, value: str) -> None:
    rejection = reject(**{column: value})

    assert rejection.reason is RejectReason.MISSING_REQUIRED
    assert rejection.column_name == column
    assert rejection.raw_value == value


def test_invalid_value_in_optional_column_is_still_rejected() -> None:
    assert reject(Age="thirty").reason is RejectReason.NOT_NUMERIC
    assert reject(Heart_Rate="350").reason is RejectReason.OUT_OF_RANGE


# --- rejections --------------------------------------------------------------------------


@pytest.mark.parametrize("count", [13, 17, 1, 0])
def test_wrong_number_of_fields_is_rejected(count: int) -> None:
    fields = (row() + ["extra"])[:count]

    rejection = parse_row(7, fields)

    assert isinstance(rejection, Rejection)
    assert rejection.line_number == 7
    assert rejection.reason is RejectReason.COLUMN_COUNT
    assert rejection.column_name is None
    assert rejection.raw_value is None
    assert rejection.raw_fields == tuple(fields)
    assert str(count) in rejection.detail


def test_row_with_several_invalid_columns_is_one_rejection_naming_all() -> None:
    rejection = reject(Smoking="2", Age="thirty", BMI="0")

    # The main reason is the first invalid column in header order.
    assert rejection.reason is RejectReason.NOT_NUMERIC
    assert rejection.column_name == "Age"
    assert rejection.raw_value == "thirty"
    for column in ("Age", "BMI", "Smoking"):
        assert column in rejection.detail


def test_rejection_keeps_the_original_untrimmed_value_and_row() -> None:
    fields = row(Age=" thirty ", Country=" Germany  ")

    rejection = parse_row(9, fields)

    assert isinstance(rejection, Rejection)
    assert rejection.raw_value == " thirty "
    assert rejection.raw_fields == tuple(fields)


def test_out_of_range_rejection_reports_the_raw_text_not_the_parsed_number() -> None:
    assert reject(Heart_Rate=" 350").raw_value == " 350"


# --- header ------------------------------------------------------------------------------


def test_expected_header_is_accepted() -> None:
    check_header(list(EXPECTED_HEADER))


@pytest.mark.parametrize(
    "header",
    [
        [],
        list(EXPECTED_HEADER[:-1]),
        [*EXPECTED_HEADER, "Extra"],
        [EXPECTED_HEADER[1], EXPECTED_HEADER[0], *EXPECTED_HEADER[2:]],
        [name.lower() for name in EXPECTED_HEADER],
    ],
)
def test_unexpected_header_is_an_error_showing_expected_and_found(header: list[str]) -> None:
    with pytest.raises(HeaderError) as error:
        check_header(header)

    message = str(error.value)
    assert "Alcohol_Consumption" in message
    assert repr(header) in message


# --- characterization against the real file ----------------------------------------------


def test_real_dataset_counts() -> None:
    """Pins what the rules do to data/coffee_health_raw.csv (no database involved)."""
    path = Path(__file__).resolve().parent.parent / "data" / "coffee_health_raw.csv"
    read = blank = fixed = with_missing = 0
    reasons: Counter[RejectReason] = Counter()
    columns: Counter[str | None] = Counter()

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        check_header(next(reader))
        for fields in reader:
            if not fields:
                blank += 1
                continue
            read += 1
            result = parse_row(reader.line_num, fields)
            if isinstance(result, Rejection):
                reasons[result.reason] += 1
                columns[result.column_name] += 1
            else:
                fixed += bool(result.fixed)
                with_missing += bool(result.missing)

    assert read == 10_012
    assert blank == 3
    assert reasons == {
        RejectReason.OUT_OF_RANGE: 6,
        RejectReason.NOT_NUMERIC: 5,
        RejectReason.COLUMN_COUNT: 3,
        RejectReason.INVALID_BOOLEAN: 2,
    }
    assert columns == {None: 3, "Age": 5, "Sleep_Hours": 3, "Heart_Rate": 2, "Smoking": 2, "BMI": 1}
    assert read - sum(reasons.values()) == 9_996
    assert fixed == 56
    assert with_missing == 9
