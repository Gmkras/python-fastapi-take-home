"""Query parameter validation and SQL building. Pure: these tests need no database.

Values are given as strings (and lists of strings) because that is how they arrive from a URL.
"""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.filters import (
    GROUP_BY,
    GroupBy,
    RecordFilters,
    RecordListParams,
    RejectListParams,
    RejectSummaryParams,
    SummaryParams,
    build_reject_where,
    build_where,
)
from app.validation import Gender, RejectReason, SleepQuality, StressLevel


def errors(model: type[BaseModel], data: dict[str, object]) -> dict[str, str]:
    """Validate `data` and return {parameter: error type}. Empty when the data is valid."""
    try:
        model.model_validate(data)
    except ValidationError as error:
        return {str(item["loc"][0]): item["type"] for item in error.errors()}
    return {}


def message(model: type[BaseModel], data: dict[str, object], parameter: str) -> str:
    """The error message reported for `parameter`."""
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    return next(e["msg"] for e in caught.value.errors() if e["loc"][0] == parameter)


# --- US1: pagination parameters ----------------------------------------------------------


def test_pagination_defaults() -> None:
    params = RecordListParams.model_validate({})

    assert (params.limit, params.after_id) == (50, 0)


@pytest.mark.parametrize("value", ["1", "200"])
def test_page_size_limits_are_inclusive(value: str) -> None:
    assert errors(RecordListParams, {"limit": value}) == {}


@pytest.mark.parametrize("value", ["0", "201", "abc", "1.5", "-5"])
def test_page_size_outside_its_limits_is_rejected(value: str) -> None:
    assert list(errors(RecordListParams, {"limit": value})) == ["limit"]


@pytest.mark.parametrize("value", ["0", "10000"])
def test_cursor_accepts_any_non_negative_integer(value: str) -> None:
    assert RecordListParams.model_validate({"after_id": value}).after_id == int(value)


@pytest.mark.parametrize("value", ["-1", "abc", "1.5"])
def test_cursor_that_is_not_a_non_negative_integer_is_rejected(value: str) -> None:
    assert list(errors(RecordListParams, {"after_id": value})) == ["after_id"]


def test_unknown_parameter_is_an_error_not_ignored() -> None:
    assert errors(RecordListParams, {"min_agee": "30"}) == {"min_agee": "extra_forbidden"}


def test_no_filters_build_no_conditions() -> None:
    where, params = build_where(RecordListParams())

    assert where.as_string() == ""
    assert params == []


# --- US2: filters ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("germany", "Germany"),
        (" SPAIN ", "Spain"),
        ("usa", "USA"),
        ("South KOREA", "South Korea"),
        ("Atlantis ", "Atlantis"),  # unknown: trimmed and kept, it simply matches nothing
        ("", ""),
        ("   ", ""),
    ],
)
def test_country_is_trimmed_and_matched_ignoring_case(given: str, expected: str) -> None:
    assert RecordFilters.model_validate({"country": [given]}).country == [expected]


def test_repeated_values_of_a_filter_are_accepted() -> None:
    filters = RecordFilters.model_validate({"country": ["Germany", "germany"]})

    assert filters.country == ["Germany", "Germany"]


def test_categories_are_matched_ignoring_case_and_surrounding_spaces() -> None:
    filters = RecordFilters.model_validate(
        {"gender": ["female", " MALE "], "sleep_quality": ["GOOD"], "stress_level": ["low"]}
    )

    assert filters.gender == [Gender.FEMALE, Gender.MALE]
    assert filters.sleep_quality == [SleepQuality.GOOD]
    assert filters.stress_level == [StressLevel.LOW]


def test_boolean_and_range_values_are_typed() -> None:
    filters = RecordFilters.model_validate(
        {"smoking": "true", "min_age": "30", "min_coffee_intake": "2.5", "max_bmi": "30"}
    )

    assert filters.smoking is True
    assert filters.min_age == 30
    assert isinstance(filters.min_coffee_intake, Decimal)
    assert (filters.min_coffee_intake, filters.max_bmi) == (Decimal("2.5"), Decimal("30"))
    assert RecordFilters.model_validate({"smoking": "false"}).smoking is False


@pytest.mark.parametrize(
    ("data", "condition", "parameters"),
    [
        ({"country": ["germany", "Spain"]}, '"country" = any(%s)', [["Germany", "Spain"]]),
        ({"gender": ["female"]}, '"gender" = any(%s)', [["Female"]]),
        ({"sleep_quality": ["good", "Poor"]}, '"sleep_quality" = any(%s)', [["Good", "Poor"]]),
        ({"stress_level": ["High"]}, '"stress_level" = any(%s)', [["High"]]),
        ({"smoking": "true"}, '"smoking" = %s', [True]),
        ({"smoking": "false"}, '"smoking" = %s', [False]),
        ({"min_age": "30"}, '"age" >= %s', [30]),
        ({"max_age": "40"}, '"age" <= %s', [40]),
        ({"min_coffee_intake": "1.5"}, '"coffee_intake" >= %s', [Decimal("1.5")]),
        ({"max_coffee_intake": "4"}, '"coffee_intake" <= %s', [Decimal("4")]),
        ({"min_bmi": "18.5"}, '"bmi" >= %s', [Decimal("18.5")]),
        ({"max_bmi": "30"}, '"bmi" <= %s', [Decimal("30")]),
    ],
)
def test_each_filter_builds_one_condition(
    data: dict[str, object], condition: str, parameters: list[object]
) -> None:
    where, values = build_where(RecordFilters.model_validate(data))

    assert where.as_string() == condition
    assert values == parameters


def test_filters_are_joined_with_and_in_a_fixed_order() -> None:
    filters = RecordFilters.model_validate(
        {"max_bmi": "30", "smoking": "true", "country": ["Germany"], "min_age": "30"}
    )

    where, values = build_where(filters)

    assert where.as_string() == (
        '"country" = any(%s) and "smoking" = %s and "age" >= %s and "bmi" <= %s'
    )
    assert values == [["Germany"], True, 30, Decimal("30")]


def test_client_values_never_reach_the_sql_text() -> None:
    hostile = "x'; drop table health_records; --"

    where, values = build_where(RecordFilters.model_validate({"country": [hostile]}))

    assert where.as_string() == '"country" = any(%s)'
    assert values == [[hostile]]


# --- US3: errors -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["age", "coffee_intake", "bmi"])
def test_maximum_below_minimum_is_reported_on_the_maximum(name: str) -> None:
    data: dict[str, object] = {f"min_{name}": "50", f"max_{name}": "20"}

    assert errors(RecordFilters, data) == {f"max_{name}": "range_inverted"}
    assert f"min_{name}" in message(RecordFilters, data, f"max_{name}")


@pytest.mark.parametrize(
    "data",
    [{"min_age": "30", "max_age": "30"}, {"max_age": "20"}, {"min_age": "50"}],
)
def test_equal_bounds_and_single_bounds_are_valid(data: dict[str, object]) -> None:
    assert errors(RecordFilters, data) == {}


def test_invalid_minimum_is_the_only_error_of_its_range() -> None:
    assert errors(RecordFilters, {"min_age": "abc", "max_age": "20"}) == {"min_age": "int_parsing"}


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("gender", "Robot"), ("sleep_quality", "Great"), ("stress_level", "Extreme")],
)
def test_unknown_category_is_rejected(parameter: str, value: str) -> None:
    assert errors(RecordFilters, {parameter: [value]}) == {parameter: "enum"}


def test_unknown_category_error_lists_the_accepted_values() -> None:
    text = message(RecordFilters, {"gender": ["Robot"]}, "gender")

    assert all(value in text for value in ("Male", "Female", "Other"))


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("min_bmi", "abc"),
        ("min_age", "30.5"),  # an age is a whole number
        ("smoking", "maybe"),
        ("min_bmi", "NaN"),  # Pydantic already refuses these; the test pins that down
        ("max_bmi", "Infinity"),
    ],
)
def test_malformed_value_is_rejected(parameter: str, value: str) -> None:
    assert list(errors(RecordFilters, {parameter: value})) == [parameter]


def test_every_invalid_parameter_is_reported_in_the_same_error() -> None:
    data: dict[str, object] = {
        "limit": "0",
        "gender": ["Robot"],
        "min_age": "50",
        "max_age": "20",
        "min_agee": "1",
    }

    assert errors(RecordListParams, data) == {
        "limit": "greater_than_equal",
        "gender": "enum",
        "max_age": "range_inverted",  # reported together with the others, not after them
        "min_agee": "extra_forbidden",
    }


# --- US4: summary parameters -------------------------------------------------------------


def test_summary_requires_a_grouping() -> None:
    assert errors(SummaryParams, {}) == {"group_by": "missing"}


@pytest.mark.parametrize(
    "value", ["country", "gender", "sleep_quality", "stress_level", "coffee_cups"]
)
def test_summary_accepts_its_five_groupings(value: str) -> None:
    assert SummaryParams.model_validate({"group_by": value}).group_by == value


def test_unknown_grouping_is_rejected() -> None:
    assert errors(SummaryParams, {"group_by": "planet"}) == {"group_by": "enum"}


def test_summary_takes_the_same_filters_as_the_list() -> None:
    data: dict[str, object] = {"country": ["germany"], "min_age": "30", "smoking": "true"}

    summary_where, summary_values = build_where(
        SummaryParams.model_validate({**data, "group_by": "gender"})
    )
    list_where, list_values = build_where(RecordListParams.model_validate(data))

    assert summary_where.as_string() == list_where.as_string()
    assert summary_values == list_values


def test_summary_has_no_pagination_parameters() -> None:
    data: dict[str, object] = {"group_by": "gender", "limit": "10", "after_id": "5"}

    assert errors(SummaryParams, data) == {
        "limit": "extra_forbidden",
        "after_id": "extra_forbidden",
    }


def test_every_grouping_maps_to_a_fixed_sql_expression() -> None:
    assert GROUP_BY.keys() == set(GroupBy)
    assert GROUP_BY[GroupBy.GENDER].as_string() == '"gender"'
    assert GROUP_BY[GroupBy.COFFEE_CUPS].as_string() == "floor(coffee_intake)::int"


# --- US5: parameters of the rows that were not loaded ------------------------------------


def test_reject_list_defaults() -> None:
    params = RejectListParams.model_validate({})

    assert params.limit == 50
    assert (params.after_source_file, params.after_line_number) == (None, None)
    assert (params.reason, params.source_file) == (None, None)


@pytest.mark.parametrize("value", ["0", "201", "abc"])
def test_reject_page_size_has_the_same_limits_as_the_record_list(value: str) -> None:
    assert list(errors(RejectListParams, {"limit": value})) == ["limit"]


def test_reject_cursor_takes_both_of_its_parts() -> None:
    params = RejectListParams.model_validate(
        {"after_source_file": "a.csv", "after_line_number": "4"}
    )

    assert (params.after_source_file, params.after_line_number) == ("a.csv", 4)


@pytest.mark.parametrize("data", [{"after_line_number": "4"}, {"after_source_file": "a.csv"}])
def test_incomplete_cursor_is_always_reported_on_the_line_number(data: dict[str, object]) -> None:
    text = message(RejectListParams, data, "after_line_number")

    assert errors(RejectListParams, data) == {"after_line_number": "cursor_incomplete"}
    assert "after_source_file" in text
    assert "after_line_number" in text


def test_incomplete_cursor_is_reported_together_with_other_errors() -> None:
    data: dict[str, object] = {"after_source_file": "a.csv", "limit": "0"}

    assert errors(RejectListParams, data) == {
        "limit": "greater_than_equal",
        "after_line_number": "cursor_incomplete",
    }


def test_negative_cursor_line_is_rejected() -> None:
    data: dict[str, object] = {"after_source_file": "a.csv", "after_line_number": "-1"}

    assert errors(RejectListParams, data) == {"after_line_number": "greater_than_equal"}


def test_reasons_are_matched_ignoring_case_and_surrounding_spaces() -> None:
    params = RejectListParams.model_validate({"reason": ["not_numeric", " OUT_OF_RANGE "]})

    assert params.reason == [RejectReason.NOT_NUMERIC, RejectReason.OUT_OF_RANGE]


def test_unknown_reason_is_rejected() -> None:
    assert errors(RejectListParams, {"reason": ["typo"]}) == {"reason": "enum"}


def test_source_file_is_an_exact_name_kept_as_given() -> None:
    assert RejectListParams.model_validate({"source_file": " My.CSV "}).source_file == " My.CSV "


@pytest.mark.parametrize("model", [RejectListParams, RejectSummaryParams])
def test_reject_routes_refuse_unknown_parameters(model: type[BaseModel]) -> None:
    assert errors(model, {"line": "5"}) == {"line": "extra_forbidden"}


def test_no_reject_arguments_build_no_conditions() -> None:
    where, values = build_reject_where()

    assert where.as_string() == ""
    assert values == []


@pytest.mark.parametrize(
    ("arguments", "condition", "parameters"),
    [
        (
            {"after": ("test_api_a.csv", 4)},
            '("source_file", "line_number") > (%s, %s)',
            ["test_api_a.csv", 4],
        ),
        (
            {"reasons": [RejectReason.NOT_NUMERIC, RejectReason.OUT_OF_RANGE]},
            '"reason" = any(%s)',
            [["not_numeric", "out_of_range"]],
        ),
        ({"source_file": "test_api_a.csv"}, '"source_file" = %s', ["test_api_a.csv"]),
    ],
)
def test_each_reject_argument_builds_one_condition(
    arguments: dict[str, Any], condition: str, parameters: list[object]
) -> None:
    where, values = build_reject_where(**arguments)

    assert where.as_string() == condition
    assert values == parameters


def test_reject_conditions_are_joined_with_and_in_a_fixed_order() -> None:
    hostile = "x'; drop table load_rejects; --"

    where, values = build_reject_where(
        source_file=hostile, reasons=[RejectReason.NOT_NUMERIC], after=("a.csv", 4)
    )

    assert where.as_string() == (
        '("source_file", "line_number") > (%s, %s) and "reason" = any(%s) and "source_file" = %s'
    )
    assert values == ["a.csv", 4, ["not_numeric"], hostile]  # client values only as parameters
