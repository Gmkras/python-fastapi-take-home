# Notes

A loader and a read API for the coffee health dataset. With `data/coffee_health_raw.csv` the
loader stores **9,984 records** and explains each of the **28 rows** it did not load.

Every figure below was measured on my machine (Ryzen 7 3700X, 32 GB, Windows 11, Postgres 17 in
Docker). The reasoning behind each decision, with the alternatives I discarded, is in `specs/`.

## 1. Schema

`db/init/002_health_records.sql` and `003_indexes.sql`

- **`health_records`**, one row per person. The source `ID` is the primary key, which is what
  lets a re-load update by ID instead of inserting again.
- One-decimal measures are `numeric(5,1)`, not `real`: values stay exact, so range filters and
  averages carry no floating point noise. `age` and `heart_rate` are `smallint`; the two flags
  are `boolean`.
- Categories are `text` with `CHECK (col IN (...))` rather than `ENUM` types: same guarantee,
  simpler to change. `country` only has a non-blank check; its catalog lives in the loader.
- Only `id`, `country`, `coffee_intake` and `caffeine_mg` are `NOT NULL`: they identify the
  record and are the subject of the dataset. The other 12 columns can be missing in the source
  and are stored as `NULL`, never imputed.
- Every range and domain the loader enforces is also a `CHECK`. The database is the second
  barrier, not the first.
- **`load_rejects`**, one row per source line that was not loaded, keyed by
  `(source_file, line_number)`: reason, column, original value, detail and the original fields.
- Indexes: the two primary keys and `(country, id)`. Why only that one is in section 5.

## 2. Cleaning and validation

`app/validation.py`: a Pydantic model with its own strict parsers, tested without a database.
Pydantic's default coercion would accept `on` as a boolean or `1e3` as a number.

| Fixed, then loaded | Rejected |
|---|---|
| Surrounding spaces or tabs (`' Germany  '`) | Wrong number of fields (13 or 17 instead of 16) |
| Case of categories and countries (`MALE`, `uk`) | Text or a sign in a number (`thirty`, `4O`, `-3`) |
| Boolean synonyms (`Y`, `yes`, `false`) | Number outside its plausible range |
| More than one decimal, rounded half up | Unknown category or country; a boolean such as `2` |
| Missing marker (empty, `n/a`, `?`, `unknown`) in an optional column, stored as `NULL` | Missing marker in one of the four required columns |

Nothing is guessed (`4O` is not read as 40) and one invalid column rejects the whole row.

Plausible ranges, inclusive. They are wider than the data on purpose: they only exclude what is
impossible or clearly a capture error.

| Age | Coffee_Intake | Caffeine_mg | Sleep_Hours | BMI | Heart_Rate | Physical_Activity_Hours |
|---|---|---|---|---|---|---|
| 18–120 | 0–20 | 0–2000 | 1–16 | 10–80 | 30–200 | 0–80 |

Result: 10,012 rows read, 9,984 loaded (56 of them after a fix, 9 with a missing optional
value) and 28 not loaded:

| Reason | Rows | Values |
|---|---|---|
| `out_of_range` | 6 | Age 150, 212; Sleep_Hours 25.0; Heart_Rate 0, 350; BMI 0 |
| `not_numeric` | 5 | Age `thirty`, `4O`, `-3`; Sleep_Hours `seven`, `-1` |
| `column_count` | 3 | 13 or 17 fields |
| `invalid_boolean` | 2 | Smoking `-1`, `2` |
| `duplicate_exact` | 8 | See section 3 |
| `duplicate_conflict` | 4 | See section 3 |

Each one, with its line number and reason:

```sql
select line_number, reason, column_name, raw_value, detail
from load_rejects where source_file = 'coffee_health_raw.csv' order by line_number;
```

The loader prints these counts and commits only if they reconcile with what the database
holds: rows read = rejected + duplicates + loaded.

## 3. Duplicates

12 IDs appear twice. **The last valid occurrence in the file wins**: a later line is taken as a
correction, which is also the rule when a newer file is loaded. The earlier line is not loaded
and is recorded as `duplicate_exact` (8, identical once normalized) or `duplicate_conflict` (4,
which differ in age and heart rate) together with the line that replaced it, so no conflict is
resolved silently. An invalid occurrence is a rejection and never wins. I discarded "keep the
first" and "load neither".

## 4. Filtering and pagination

`GET /records`, used from `/docs`. Parameters and their SQL are in `app/filters.py`.

- **Filters**: any of several values for `country`, `gender`, `sleep_quality` and
  `stress_level` (repeat the parameter; case and surrounding spaces are ignored); `smoking`;
  and inclusive `min_`/`max_` ranges for `age`, `coffee_intake` and `bmi`. Different filters are
  combined with AND. The list is short on purpose: every filter is a question about an index.
- A filter leaves out the records that have no value in that column. In a response a missing
  value is `null`, and numbers are JSON numbers.
- A misspelled parameter (`min_agee`) is a 422 instead of being ignored, and every invalid
  parameter of a request is reported in the same response, including `max_age` below `min_age`.
  An unknown country is an empty page, not an error.
- The filters become SQL in one pure function, tested without a database. Client values only
  ever travel as query parameters.
- **Pagination is by cursor (keyset), not by page number**:
  `WHERE id > :after_id ORDER BY id LIMIT :n`. The response carries `next_after_id`, null on the
  last page. 50 records per page by default, 200 at most. There is no `count(*)` per page: the
  total comes from `GET /records/summary`, which returns the count and three averages per group.

`GET /rejects` and `GET /rejects/summary` expose the rows that were not loaded.

## 5. Scale

**Loader.** It streams the file, validates in chunks of 10,000 rows, sends each chunk with
`COPY` to a temporary table, and resolves duplicates and the upsert (`MERGE`, skipping rows that
did not change) in SQL, in one transaction.

| File | Time | Peak memory |
|---|---|---|
| Real file, 10,012 rows | 0.5 s | 55 MiB |
| 1,001,200 rows | 50 s, about 20,000 rows/s | 65 MiB |

Memory depends on the chunk, not on the file: a 50,000-row chunk used 142 MiB and was no faster.
About half of the time is validation in Python. 10 million rows would take 8 to 9 minutes.

**Deep pages.** `EXPLAIN (ANALYZE, BUFFERS)` on 1 million rows, same page at the end of the
table. Both plans are an index scan on the primary key; what changes is how much they read.

| Query | Rows read | Buffers | Time |
|---|---|---|---|
| `WHERE id > 990005000 ORDER BY id LIMIT 51` | 51 | 5 | 0.06 ms |
| `ORDER BY id LIMIT 51 OFFSET 993400` | 993,451 | 17,427 | 122 ms |

End to end, the first page and the last page both answer in about 7 ms.

**Indexes.** The primary key serves the list and most filters: scanning from the cursor and
skipping the rows that do not match fills a page quickly (0.2 ms for one country, 5% of the
table). The one extra index, `(country, id)`, is for a country with few rows or none, such as a
misspelled name: without it that request reads the whole table (110 ms), with it 0.09 ms. Its
cost: the insert step alone is 39% slower, although the full load did not change measurably
(52.8 s), and it takes 35 MB per 1.2 million rows.

**Connections.** A bounded pool (10 connections) and plain `def` routes. A request waits at most
5 s for a connection and a query runs at most 5 s; past either limit, or with the database down,
the API answers `503` instead of hanging. The pool tests a connection before lending it, so a
database restart does not fail a request. All of it is configurable (`.env.example`). Sizing
rule: worker processes × pool size must stay below Postgres' `max_connections`.

**Trade-offs and assumptions**

- One load at a time, in a single transaction: a failure leaves nothing behind, at the cost of
  a long transaction. For a much larger initial load I would commit per chunk with a table of
  runs to resume from, validate in several processes and create secondary indexes afterwards.
- A filter without an index that matches almost nothing scans to the end (48 ms on 1 million
  rows), and the unfiltered summary reads the whole table (183 ms, so about 5 s near 27 million
  rows). Both are cut by the 5 s limit. At that size I would add a `(column, id)` index for a
  filter proven to be frequent and selective, and a small table of aggregates refreshed by the
  loader. Neither is built: nothing needs them today.
- For more traffic the next step is PgBouncer in front of Postgres, or async routes.
- "Last occurrence wins" assumes the file is in export order, and that an ID is the same person
  across files.

## 6. Lint exceptions

One: `# noqa: E402` on the two `app` imports in `scripts/load_data.py`. `make load` runs the
script by path, which puts `scripts/` on `sys.path` instead of the repository root, so the root
is added before those imports. The alternative was changing the Makefile. No `type: ignore`.

## 7. How AI was used

I built this with an AI coding assistant (Claude Code), following spec-driven development with
[spec-kit](https://github.com/github/spec-kit): a constitution with the project's principles
and then, for each feature, specify → clarify → plan → tasks → analyze → implement. The
artifacts are in `specs/` and `.specify/memory/constitution.md`.

I set the rules and decided the ambiguous cases in the clarify step: what to reject, which
duplicate wins, what to do with missing values, how filters treat case, the time limits. The
assistant drafted specs, plans and tasks from those decisions, wrote the tests before the code,
and ran the measurements. I ran and checked every step against my plan and tested the API by
hand, which is how the 503 after a database restart was found and fixed.
