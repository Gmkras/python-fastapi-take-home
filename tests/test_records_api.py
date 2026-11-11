"""Read API tests against the real database.

Each test inserts its own committed rows through the `seed` fixture (IDs far above the real
dataset, countries that do not exist in it) and the fixture deletes exactly those rows. So the
tests neither depend on the loaded data nor change it. They assume no real row has an
ID of 9,000,000 or more.
"""

import time
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import Record
from tests.conftest import Seed, record_row, reject_row

FIRST_ID = 9_000_001
AFTER_REAL_DATA = FIRST_ID - 1  # as a cursor, skips every row of the real dataset


def records_page(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/records", params={"after_id": AFTER_REAL_DATA, **params})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def walk_records(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    """Follow next_after_id until it is null and return every item received."""
    items: list[dict[str, Any]] = []
    after: int = params.pop("after_id", AFTER_REAL_DATA)
    for _ in range(100):
        body = records_page(client, **{**params, "after_id": after})
        items += body["items"]
        if body["next_after_id"] is None:
            return items
        after = body["next_after_id"]
    raise AssertionError("pagination did not end")


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


# --- US1: paginated list -----------------------------------------------------------------


def test_page_is_bounded_ordered_and_carries_a_cursor(client: TestClient, seed: Seed) -> None:
    seed.records([record_row(FIRST_ID + n) for n in (3, 0, 4, 1, 2)])  # inserted out of order

    body = records_page(client, limit=2)

    assert ids(body["items"]) == [FIRST_ID, FIRST_ID + 1]
    assert body["next_after_id"] == FIRST_ID + 1
    assert body["limit"] == 2


def test_following_the_cursor_returns_every_record_exactly_once(
    client: TestClient, seed: Seed
) -> None:
    seed.records([record_row(FIRST_ID + n) for n in range(5)])

    seen = walk_records(client, limit=2)

    assert ids(seen) == [FIRST_ID + n for n in range(5)]


def test_cursor_is_null_when_the_last_page_is_exactly_full(client: TestClient, seed: Seed) -> None:
    seed.records([record_row(FIRST_ID + n) for n in range(5)])

    body = records_page(client, limit=5)

    assert len(body["items"]) == 5
    assert body["next_after_id"] is None


def test_cursor_past_the_end_is_an_empty_page_not_an_error(client: TestClient, seed: Seed) -> None:
    seed.records([record_row(FIRST_ID + n) for n in range(5)])

    body = records_page(client, after_id=FIRST_ID + 4)

    assert body["items"] == []
    assert body["next_after_id"] is None


def test_without_parameters_the_page_holds_at_most_50_records(client: TestClient) -> None:
    response = client.get("/records")

    body = response.json()
    assert response.status_code == 200
    assert body["limit"] == 50
    assert len(body["items"]) <= 50
    assert ids(body["items"]) == sorted(ids(body["items"]))


def test_missing_optional_values_are_null(client: TestClient, seed: Seed) -> None:
    seed.records([record_row(FIRST_ID, age=None, bmi=None, gender=None, smoking=None)])

    item = records_page(client)["items"][0]

    assert (item["age"], item["bmi"], item["gender"], item["smoking"]) == (None, None, None, None)
    assert item["country"] == "Testland"
    assert item["heart_rate"] == 70


def test_values_keep_their_json_types(client: TestClient, seed: Seed) -> None:
    seed.records([record_row(FIRST_ID, smoking=True)])

    item = records_page(client)["items"][0]

    assert item.keys() == Record.model_fields.keys()  # the 16 source fields, no loaded_at
    for column in ("coffee_intake", "caffeine_mg", "sleep_hours", "bmi", "physical_activity_hours"):
        assert isinstance(item[column], float), column  # a JSON number, never "3.5"
    assert item["coffee_intake"] == 3.5
    assert item["smoking"] is True  # a JSON boolean, never 1
    assert item["alcohol_consumption"] is False
    assert isinstance(item["age"], int)
    assert item["health_issues"] == "None"  # a real value of the domain, not a missing one


def test_page_size_above_the_maximum_is_rejected(client: TestClient) -> None:
    response = client.get("/records", params={"limit": 201})

    assert response.status_code == 422


# --- US2: filters ------------------------------------------------------------------------


def seed_people(seed: Seed) -> None:
    """Five people in two test countries; the last one has no age, BMI or smoking value."""
    seed.records(
        [
            person(0, country="Testland", gender="Female", age=25, smoking=True, bmi="22.0"),
            person(1, country="Testland", gender="Male", age=30, smoking=False, bmi="27.5"),
            person(2, country="Testonia", gender="Female", age=40, smoking=False, bmi="31.0"),
            person(3, country="Testonia", gender="Other", age=41, smoking=True, bmi="19.0"),
            person(4, country="Testland", gender="Female", age=None, smoking=None, bmi=None),
        ]
    )


def person(number: int, *, bmi: str | None = "24.9", **columns: object) -> dict[str, object]:
    coffee = Decimal(number)  # 0.0, 1.0, 2.0, 3.0, 4.0 cups
    return record_row(
        FIRST_ID + number,
        bmi=None if bmi is None else Decimal(bmi),
        coffee_intake=coffee,
        **columns,
    )


def found(client: TestClient, **params: Any) -> list[int]:
    """Which of the seeded people a filter returns, walking every page."""
    return [item["id"] - FIRST_ID for item in walk_records(client, **params)]


def test_several_values_of_one_filter_match_any_of_them(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    assert found(client, country=["Testland", "Testonia"]) == [0, 1, 2, 3, 4]
    assert found(client, country="Testonia") == [2, 3]
    assert found(client, gender=["Male", "Other"]) == [1, 3]


def test_different_filters_must_all_match(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    assert found(client, country="Testland", gender="Female") == [0, 4]
    assert found(client, country="Testonia", gender="Female", smoking="false") == [2]


def test_category_filters_ignore_case(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    assert found(client, gender="female") == found(client, gender="Female") == [0, 2, 4]


def test_boolean_filter(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    assert found(client, smoking="true") == [0, 3]
    assert found(client, smoking="false") == [1, 2]


def test_range_filters_include_both_ends_and_may_be_open(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    assert found(client, min_age=30, max_age=40) == [1, 2]
    assert found(client, min_age=40) == [2, 3]
    assert found(client, max_age=30) == [0, 1]
    assert found(client, min_bmi="27.5") == [1, 2]
    assert found(client, max_coffee_intake="2.0") == [0, 1, 2]
    assert found(client, min_coffee_intake="2.5", max_coffee_intake="4") == [3, 4]


def test_a_filter_leaves_out_records_without_that_value(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    assert 4 in found(client)
    assert 4 not in found(client, min_age=0)
    assert 4 not in found(client, max_age=120)
    assert 4 not in found(client, min_bmi="0")


def test_filters_without_matches_return_an_empty_page(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    for params in ({"country": "Atlantis"}, {"country": ""}, {"min_age": 500}):
        body = records_page(client, **params)
        assert (body["items"], body["next_after_id"]) == ([], None), params


def test_cursor_walks_only_the_records_that_match(client: TestClient, seed: Seed) -> None:
    seed_people(seed)

    assert found(client, country="Testland", limit=1) == [0, 1, 4]


def test_record_added_during_a_walk_shows_up_later_and_nothing_repeats(
    client: TestClient, seed: Seed
) -> None:
    seed.records([record_row(FIRST_ID + n) for n in range(3)])
    first = records_page(client, limit=2)

    seed.records([record_row(FIRST_ID + 3)])
    rest = walk_records(client, limit=2, after_id=first["next_after_id"])

    assert ids(first["items"]) + ids(rest) == [FIRST_ID + n for n in range(4)]


# --- US3: errors -------------------------------------------------------------------------


def invalid_parameters(client: TestClient, url: str) -> set[str]:
    """The query parameters named by the 422 of `url`. Also checks that it carries no data."""
    response = client.get(url)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body.keys() == {"detail"}
    return {item["loc"][1] for item in body["detail"] if item["loc"][0] == "query"}


@pytest.mark.parametrize(
    ("query", "parameter"),
    [
        ("min_age=50&max_age=20", "max_age"),
        ("gender=Robot", "gender"),
        ("limit=0", "limit"),
        ("limit=201", "limit"),
        ("limit=abc", "limit"),
        ("after_id=-1", "after_id"),
        ("after_id=abc", "after_id"),
        ("min_agee=30", "min_agee"),
        ("min_bmi=abc", "min_bmi"),
        ("min_age=30.5", "min_age"),
        ("smoking=maybe", "smoking"),
    ],
)
def test_invalid_parameter_is_a_422_that_names_it(
    client: TestClient, query: str, parameter: str
) -> None:
    assert invalid_parameters(client, f"/records?{query}") == {parameter}


def test_inverted_range_message_names_both_bounds(client: TestClient) -> None:
    detail = client.get("/records?min_age=50&max_age=20").json()["detail"]

    assert "min_age" in detail[0]["msg"]
    assert "max_age" in detail[0]["msg"]


def test_all_invalid_parameters_are_named_in_one_response(client: TestClient) -> None:
    url = "/records?min_age=50&max_age=20&limit=0&min_agee=1"

    assert invalid_parameters(client, url) == {"max_age", "limit", "min_agee"}


# --- US4: summary ------------------------------------------------------------------------
#
# The summary has no cursor, so each case isolates itself from the loaded data by filtering
# on the test countries.


def seed_measures(seed: Seed) -> None:
    """Five people in Testland and one in Testonia, with some missing values."""

    def row(
        number: int, gender: str | None, sleep: str | None, coffee: str, bmi: str | None
    ) -> dict[str, object]:
        return record_row(
            FIRST_ID + number,
            gender=gender,
            sleep_hours=None if sleep is None else Decimal(sleep),
            coffee_intake=Decimal(coffee),
            bmi=None if bmi is None else Decimal(bmi),
        )

    seed.records(
        [
            row(0, "Female", "6.0", "2.0", "20.0"),
            row(1, "Female", "7.0", "2.9", "21.0"),
            row(2, "Female", None, "3.0", "22.5"),
            row(3, "Male", "8.0", "0.5", None) | {"smoking": True},
            row(4, None, "5.0", "1.0", "30.0"),
            row(5, "Male", "9.0", "4.0", "25.0") | {"country": "Testonia"},
        ]
    )


def summary(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/records/summary", params=params)
    assert response.status_code == 200, response.text
    groups: list[dict[str, Any]] = response.json()
    return groups


def counts(groups: list[dict[str, Any]]) -> list[tuple[Any, int]]:
    return [(group["key"], group["count"]) for group in groups]


def test_summary_counts_and_averages_each_group(client: TestClient, seed: Seed) -> None:
    seed_measures(seed)

    groups = summary(client, group_by="gender", country="Testland")

    assert groups == [
        {  # sleep: (6.0 + 7.0) / 2, the missing one is ignored; coffee: 7.9 / 3; bmi: 63.5 / 3
            "key": "Female",
            "count": 3,
            "avg_sleep_hours": 6.5,
            "avg_coffee_intake": 2.63,
            "avg_bmi": 21.17,
        },
        {  # its only record has no BMI: the average is null, not 0
            "key": "Male",
            "count": 1,
            "avg_sleep_hours": 8.0,
            "avg_coffee_intake": 0.5,
            "avg_bmi": None,
        },
        {  # records without a gender form their own group, listed last
            "key": None,
            "count": 1,
            "avg_sleep_hours": 5.0,
            "avg_coffee_intake": 1.0,
            "avg_bmi": 30.0,
        },
    ]
    assert isinstance(groups[0]["avg_bmi"], float)
    assert isinstance(groups[0]["count"], int)


@pytest.mark.parametrize("filters", [{}, {"smoking": "true"}, {"min_bmi": "21"}])
def test_summary_counts_add_up_to_the_list_with_the_same_filters(
    client: TestClient, seed: Seed, filters: dict[str, Any]
) -> None:
    seed_measures(seed)
    filters = {"country": "Testland", **filters}

    groups = summary(client, group_by="gender", **filters)

    assert sum(group["count"] for group in groups) == len(found(client, **filters))


def test_summary_by_coffee_cups_uses_one_cup_buckets(client: TestClient, seed: Seed) -> None:
    seed_measures(seed)

    groups = summary(client, group_by="coffee_cups", country="Testland")

    # 0.5 -> 0; 1.0 -> 1; 2.0 and 2.9 -> 2; 3.0 -> 3
    assert counts(groups) == [(0, 1), (1, 1), (2, 2), (3, 1)]


def test_summary_by_country(client: TestClient, seed: Seed) -> None:
    seed_measures(seed)

    groups = summary(client, group_by="country", country=["Testland", "Testonia"])

    assert counts(groups) == [("Testland", 5), ("Testonia", 1)]


def test_summary_without_matches_is_an_empty_list(client: TestClient) -> None:
    assert summary(client, group_by="gender", country="Atlantis") == []


@pytest.mark.parametrize("query", ["group_by=planet", "country=Testland"])
def test_summary_needs_a_valid_grouping(client: TestClient, query: str) -> None:
    assert invalid_parameters(client, f"/records/summary?{query}") == {"group_by"}


# --- US5: rows that were not loaded ------------------------------------------------------

FILE_A = "test_api_a.csv"
FILE_B = "test_api_b.csv"
BEFORE_TEST_FILES = {"after_source_file": FILE_A, "after_line_number": 0}  # skips real files


def seed_rejects(seed: Seed) -> None:
    """Four rejects of file A and two of file B, inserted out of order."""
    duplicate: dict[str, object] = {"column_name": None, "raw_value": None}
    seed.rejects(
        [
            reject_row(FILE_B, 7, reason="column_count", detail="expected 16 fields", **duplicate),
            reject_row(FILE_A, 9, reason="out_of_range", column_name="Heart_Rate", raw_value="350"),
            reject_row(FILE_A, 5),
            reject_row(
                FILE_A, 12, reason="duplicate_conflict", detail="superseded by line 20", **duplicate
            ),
            reject_row(FILE_A, 15),
            reject_row(FILE_B, 3),
        ]
    )


def rejects_page(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/rejects", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def lines(items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return [(item["source_file"], item["line_number"]) for item in items]


def test_rejects_of_a_file_come_in_line_order_with_their_explanation(
    client: TestClient, seed: Seed
) -> None:
    seed_rejects(seed)

    body = rejects_page(client, source_file=FILE_A)

    assert lines(body["items"]) == [(FILE_A, 5), (FILE_A, 9), (FILE_A, 12), (FILE_A, 15)]
    assert body["next_after"] is None
    assert body["items"][0] == {
        "source_file": FILE_A,
        "line_number": 5,
        "reason": "not_numeric",
        "column_name": "Age",
        "raw_value": "thirty",
        "detail": "Age='thirty': not_numeric",
    }  # exactly these six fields: no raw_fields, no loaded_at
    assert body["items"][2] == {
        "source_file": FILE_A,
        "line_number": 12,
        "reason": "duplicate_conflict",
        "column_name": None,
        "raw_value": None,
        "detail": "superseded by line 20",  # the only place that says what replaced it
    }


def test_reject_cursor_walks_every_row_once_across_files(client: TestClient, seed: Seed) -> None:
    seed_rejects(seed)
    seen: list[dict[str, Any]] = []
    cursor: dict[str, Any] | None = BEFORE_TEST_FILES

    while cursor is not None:
        body = rejects_page(client, limit=2, **cursor)
        seen += body["items"]
        next_after = body["next_after"]
        cursor = next_after and {
            "after_source_file": next_after["source_file"],
            "after_line_number": next_after["line_number"],
        }

    assert lines(seen) == [
        (FILE_A, 5),
        (FILE_A, 9),
        (FILE_A, 12),
        (FILE_A, 15),
        (FILE_B, 3),
        (FILE_B, 7),
    ]


def test_reject_cursor_is_null_exactly_when_nothing_is_left(client: TestClient, seed: Seed) -> None:
    seed_rejects(seed)

    first_of_three = rejects_page(client, limit=2, **BEFORE_TEST_FILES)
    exactly_full = rejects_page(client, limit=6, **BEFORE_TEST_FILES)

    assert first_of_three["next_after"] == {"source_file": FILE_A, "line_number": 9}
    assert len(exactly_full["items"]) == 6
    assert exactly_full["next_after"] is None


def test_rejects_can_be_filtered_by_reason(client: TestClient, seed: Seed) -> None:
    seed_rejects(seed)

    either = rejects_page(client, source_file=FILE_A, reason=["not_numeric", "out_of_range"])
    upper = rejects_page(client, source_file=FILE_A, reason="OUT_OF_RANGE")

    assert lines(either["items"]) == [(FILE_A, 5), (FILE_A, 9), (FILE_A, 15)]
    assert lines(upper["items"]) == [(FILE_A, 9)]


def test_rejects_of_an_unknown_file_are_an_empty_page(client: TestClient) -> None:
    body = rejects_page(client, source_file="no_such_file.csv")

    assert (body["items"], body["next_after"]) == ([], None)


def test_reject_counts_by_reason_most_frequent_first(client: TestClient, seed: Seed) -> None:
    seed_rejects(seed)

    response = client.get("/rejects/summary", params={"source_file": FILE_A})

    assert response.status_code == 200
    assert response.json() == [
        {"reason": "not_numeric", "count": 2},
        {"reason": "duplicate_conflict", "count": 1},  # ties are ordered by reason
        {"reason": "out_of_range", "count": 1},
    ]


@pytest.mark.parametrize(
    ("query", "parameter"),
    [
        ("reason=typo", "reason"),
        ("after_line_number=5", "after_line_number"),
        ("after_source_file=a.csv", "after_line_number"),  # an incomplete cursor, either way
        ("limit=201", "limit"),
        ("line=5", "line"),
    ],
)
def test_invalid_reject_parameter_is_a_422_that_names_it(
    client: TestClient, query: str, parameter: str
) -> None:
    assert invalid_parameters(client, f"/rejects?{query}") == {parameter}


# --- temporary errors: a busy or slow database answers 503, it never hangs ---------------
#
# Each test changes the pool settings and then opens its own TestClient, because the pool is
# created when the client starts the app.


def test_saturated_pool_answers_503_instead_of_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_pool_min_size", 1)
    monkeypatch.setattr(settings, "db_pool_max_size", 1)
    monkeypatch.setattr(settings, "db_pool_timeout_seconds", 0.3)

    with TestClient(app) as client:
        only_connection = app.state.pool.getconn()
        try:
            started = time.perf_counter()
            response = client.get("/records")
            elapsed = time.perf_counter() - started
        finally:
            app.state.pool.putconn(only_connection)
        afterwards = client.get("/records", params={"limit": 1})

    assert response.status_code == 503
    assert "retry" in response.json()["detail"]
    assert elapsed < 2
    assert afterwards.status_code == 200


def test_query_over_the_time_limit_answers_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 200)

    with (
        TestClient(app) as client,
        psycopg.connect(settings.database_url, connect_timeout=10) as other_session,
    ):
        # Another session holds the table, so the request waits as it would behind a slow
        # query. Waiting for a lock counts towards the statement time limit.
        other_session.execute("lock table health_records in access exclusive mode")
        try:
            response = client.get("/records", params={"limit": 1})
        finally:
            other_session.rollback()
        afterwards = client.get("/records", params={"limit": 1})

    assert response.status_code == 503
    assert "retry" in response.json()["detail"]
    assert afterwards.status_code == 200


def test_connection_closed_by_the_server_is_replaced_not_reported() -> None:
    with TestClient(app) as client:
        with app.state.pool.connection() as conn:
            backend = conn.info.backend_pid
        # What a database restart does to an idle pooled connection. The second argument
        # waits until that backend is really gone.
        with psycopg.connect(settings.database_url, connect_timeout=10, autocommit=True) as admin:
            admin.execute("select pg_terminate_backend(%s, 2000)", (backend,))

        response = client.get("/records", params={"limit": 1})

    assert response.status_code == 200  # the database is up: the client must not see a 503


def test_pool_connections_carry_the_time_limit_and_never_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 1234)

    with TestClient(app), app.state.pool.connection() as conn:
        limit = conn.execute("select current_setting('statement_timeout') as value").fetchone()

        assert limit == {"value": "1234ms"}
        assert conn.prepare_threshold is None


# --- interactive documentation -----------------------------------------------------------


def test_documentation_describes_every_route_and_parameter(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    paths, schemas = spec["paths"], spec["components"]["schemas"]

    def parameter(route: str, name: str) -> dict[str, Any]:
        found: dict[str, Any] = next(
            p for p in paths[route]["get"]["parameters"] if p["name"] == name
        )
        return found

    for route in ("/records", "/records/summary", "/rejects", "/rejects/summary"):
        assert list(paths[route]) == ["get"], route  # the API is read-only
        assert paths[route]["get"]["summary"]
        for documented in paths[route]["get"]["parameters"]:
            assert documented.get("description"), f"{route}: {documented['name']}"
    assert {"/health", "/notes"} <= paths.keys()  # the routes that already existed

    limit = parameter("/records", "limit")["schema"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 200, 50)
    assert schemas["Gender"]["enum"] == ["Male", "Female", "Other"]
    assert "#/components/schemas/Gender" in str(parameter("/records", "gender")["schema"])
    # What "Add string item" pre-fills in /docs: a real country, not the placeholder "string".
    for route in ("/records", "/records/summary"):
        country_values = parameter(route, "country")["schema"]["anyOf"][0]["items"]
        assert country_values["examples"] == ["Germany"], route
    assert parameter("/records/summary", "group_by")["required"] is True
    assert schemas["GroupBy"]["enum"] == [
        "country",
        "gender",
        "sleep_quality",
        "stress_level",
        "coffee_cups",
    ]
