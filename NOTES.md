# Notes

Covers the loader and the read API. The full reasoning behind each decision, with the
alternatives that were discarded, is in `specs/001-csv-loader/` and `specs/002-records-api/`.

## Schema

`db/init/002_health_records.sql`

- **`health_records`**: one row per person. The source `ID` is the primary key (`integer`),
  which is what makes re-loading an update by ID instead of a second insert.
- One-decimal measures are `numeric(5,1)` / `numeric(6,1)`, not `real`: values stay exact, so
  range filters and averages carry no floating point noise. `age` and `heart_rate` are
  `smallint`, the two flags are `boolean`.
- Categories are `text` with a `CHECK (col IN (...))` rather than native `ENUM`s: same
  guarantee, and changing a catalog is a plain `ALTER TABLE`. `country` only has a non-blank
  `CHECK`; its catalog is open-ended and enforced by the loader.
- Only `id`, `country`, `coffee_intake` and `caffeine_mg` are `NOT NULL`. The other 12 columns
  may be missing in the source and are stored as `NULL` (see below).
- Every range and domain the loader enforces is also a `CHECK`, so the database refuses bad
  data even if something bypasses the loader.
- **`load_rejects`**: one row per source line that was not loaded, keyed by
  `(source_file, line_number)`, with the reason, the offending column and value, and the
  original fields. It holds the latest load of each file.
- Indexes: the two primary keys and one more, `(country, id)`, in `db/init/003_indexes.sql`.
  Why only that one is under "Scale (API)".

## Cleaning and validation

`app/validation.py` (pure, tested without a database). Parsing is strict on purpose: Pydantic's
default coercion would accept `"on"` as a boolean or `"1e3"` and `"NaN"` as numbers.

| Fixed and loaded | Rejected |
|---|---|
| Surrounding whitespace (`' Germany  '`) | Wrong number of fields |
| Case of categories and countries (`MALE`, `uk` → `Male`, `UK`) | Text or signs in a number (`thirty`, `4O`, `-3`) |
| Boolean synonyms (`Y`, `yes`, `true`, `N`, `no`, `false`) | Number outside its plausible range |
| Extra decimals, rounded half up to one place | Value outside its catalog, unknown country |
| Missing marker (empty, `n/a`, `?`, `unknown`, `null`) in an optional column → `NULL` | Missing marker in `ID`, `Country`, `Coffee_Intake` or `Caffeine_mg` |
| | Boolean outside `0/1` and the synonyms (`2`, `-1`) |

A row is all or nothing: one invalid column rejects the whole row, and a rejected row never
touches a record that is already stored.

Numeric limits (inclusive). They are looser than the observed data on purpose: they exclude
what is impossible or clearly a capture error, not what is merely unusual.

| Column | Range | Column | Range |
|---|---|---|---|
| Age | 18–120 | BMI | 10–80 |
| Coffee_Intake | 0–20 cups/day | Heart_Rate | 30–200 bpm |
| Caffeine_mg | 0–2000 mg/day | Physical_Activity_Hours | 0–80 h/week |
| Sleep_Hours | 1–16 | ID | 1–2,147,483,647 |

Result with `data/coffee_health_raw.csv`: 10,012 rows read (plus 3 blank lines), **9,984
loaded**, 28 not loaded. 56 loaded rows needed a fix and 9 have a missing optional value.

| Reason | Rows | What |
|---|---|---|
| `out_of_range` | 6 | Age 150, 212; Sleep_Hours 25.0; Heart_Rate 0, 350; BMI 0 |
| `not_numeric` | 5 | Age `thirty`, `4O`, `-3`; Sleep_Hours `seven`, `-1` |
| `column_count` | 3 | Rows with 13 or 17 fields instead of 16 |
| `invalid_boolean` | 2 | Smoking `-1`, `2` |
| `duplicate_exact` | 8 | Earlier copy of an ID, identical to the one kept |
| `duplicate_conflict` | 4 | Earlier copy of an ID, different from the one kept |

Each of them, with line number and reason:

```sql
select line_number, reason, column_name, raw_value, detail
from load_rejects where source_file = 'coffee_health_raw.csv' order by line_number;
```

The loader prints these counts and only commits if they reconcile with what the database
holds (rows read = rejected + duplicates + loaded).

## Duplicates

When an ID appears more than once, **the last valid occurrence in the file wins**: a later
line is taken as a correction of an earlier one. The discarded occurrences are recorded in
`load_rejects` as `duplicate_exact` or `duplicate_conflict`, with the line that replaced them
(`superseded by line 3516`), so a conflict is never resolved silently. An invalid occurrence is
a rejection, not a duplicate, and does not win. This is resolved in SQL over the staged rows,
not in a Python dict, so it does not depend on the file fitting in memory.

## Filtering and pagination

Four read-only routes, all usable from `/docs`: `GET /records`, `GET /records/summary`,
`GET /rejects` and `GET /rejects/summary`.

**Filters** on `/records` (and the same ones on the summary):

| Kind | Parameters | Behaviour |
|---|---|---|
| Any of several values | `country`, `gender`, `sleep_quality`, `stress_level` | Repeat the parameter; case and surrounding spaces are ignored |
| Yes / no | `smoking` | |
| Inclusive range | `min_age`/`max_age`, `min_coffee_intake`/`max_coffee_intake`, `min_bmi`/`max_bmi` | Either end may be left open |

Different filters are combined with AND. I kept the list short on purpose: every filter is a
question about whether it deserves an index.

- A filter on a column leaves out the records that have no value in it; in a response a
  missing value is `null`.
- An unknown country is not an error, it returns an empty page. An unknown category is a 422
  that lists the accepted values.
- Parameters are a Pydantic model (`app/filters.py`), so a misspelled one (`min_agee`) is a 422
  instead of being silently ignored, and every invalid parameter of a request is reported in
  the same response, including `max_age` below `min_age`.
- The filters become SQL in one pure function, `build_where`, tested without a database. Client
  values only ever travel as query parameters.

**Pagination** is by cursor, not by page number: `WHERE id > :after_id ORDER BY id LIMIT :n`.
The response carries `next_after_id` (null on the last page) and the client sends it back as
`after_id`. Page size is 50 by default and 200 at most. One extra row is fetched to know whether
another page exists, so there is no `count(*)`.

Measured on 1 million rows, same page at the end of the table:

| | Rows read | Time |
|---|---|---|
| Cursor (`id > ...`) | 51 | 0.06 ms |
| `OFFSET 993400` | 993,451 | 122 ms |

`OFFSET` reads and throws away every earlier row, so its cost grows with the page number; the
cursor costs the same anywhere. The cursor is also stable if rows are added during a walk.
What is given up: jumping to an arbitrary page, and a total in the list (the summary gives it).

`/records/summary` groups by country, gender, sleep quality, stress level or whole cups of
coffee and returns the count and three averages per group. Records with no value to group by
form a group whose key is `null`, so the counts always add up to the list with the same filters.
`/rejects` exposes `load_rejects` with the same pagination rules.

## Scale (loader)

The file is streamed and validated in chunks of 10,000 rows, each sent with `COPY` to a
temporary table; duplicates and the upsert (`MERGE`, which skips rows that did not change) run
in SQL. Everything is one transaction, guarded by an advisory lock so loads run one at a time.

Measured on a development laptop (Windows, Postgres 17 in Docker):

| File | Time | Rows/s | Peak process memory |
|---|---|---|---|
| Real file, 10,012 rows | 0.5 s | — | 55 MiB |
| 1,001,200 rows, chunk 10,000 (default) | 50 s | ~20,000 | 65 MiB |
| 1,001,200 rows, chunk 50,000 | 50 s | ~20,000 | 142 MiB |

Memory depends on the chunk size, not on the file size; a bigger chunk bought no speed, so
the default is 10,000. About half of the time is row validation in Python, the rest is `COPY`
and the final `MERGE`. Throughput varied between 18,800 and 20,200 rows/s across runs. Time
grows linearly: 10 million rows would take roughly 8 to 9 minutes. With the API's
`(country, id)` index in place the same load took 52.8 s, inside that run-to-run variation.

Trade-offs and what I would change for tens of millions of rows:

- One transaction keeps a failed load from leaving anything behind, at the cost of a long
  transaction and a staging table about the size of the file. For very large initial loads I
  would commit per chunk and keep a table of runs to resume from.
- Validation is single-process. Splitting it across processes is the next step if throughput
  matters; it complicates line numbering and atomicity, so it was left out.
- Secondary indexes should be created after a bulk load, not maintained row by row during it.

## Scale (API)

Measured on a table of 998,400 rows (Ryzen 7 3700X, 32 GB, Windows 11, Postgres 17 in Docker).
End-to-end times include validation and serialization; they are medians of 20 requests.

| Request | Time |
|---|---|
| First page, no filters | 6.0 ms |
| Last page, no filters | 7.0 ms |
| First / last page, `country=Germany` | 7.2 / 6.7 ms |
| A country that does not exist | 7.4 ms |
| A filter with no matches and no index (`min_age=119`) | 47.5 ms |
| Summary by gender, no filters | 183 ms |

**Indexes.** The primary key serves the list and most filters: scanning from the cursor and
skipping the rows that do not match finds a page almost at once when a filter matches a fair
share of the table (a country is 5% of it; a combination matching 0.26% took 12 ms). I added
one index,
`(country, id)`, and not for the common case: for a country with few rows or none, such as a
misspelled name, the primary key scan would read the whole table before answering with an
empty page (110 ms on 1M rows without the index, 0.09 ms with it, and growing with the table).
Its cost: inserts were 39% slower in isolation, although the full load, dominated by
validation, did not change measurably; it takes 35 MB per 1.2M rows. One index per filter
would multiply that cost for filters that the primary key already serves.

**Known worst cases**, accepted and bounded by the time limit below:

- A filter without an index that matches almost nothing scans until it fills the page, or to
  the end. At tens of millions of rows that would need its own `(column, id)` index, added
  only for a filter that proves to be both frequent and selective.
- The unfiltered summary reads the whole table: about 5 s near 27 million rows on this machine.
  The answer at that size is a small table of aggregates refreshed at the end of each load. It
  is not built: nothing needs it today.

**Connections.** Routes are plain `def` functions over the synchronous pool that the repo
already had. The pool is bounded (10 connections by default) and so is time: a request waits at
most 5 s for a connection and a query may run at most 5 s (`statement_timeout`, set on the
pool's connections only, so the loader is not affected). When either limit is hit, or the
database is down, the API answers `503` instead of hanging; with the database stopped it
answered at once and recovered by itself when the database came back. The pool tests each
connection before lending it, so a connection the server closed (a restart, an idle timeout)
is replaced instead of failing a request; that costs one extra round trip, about 0.5 ms here,
which the times above do not include. All four values are settings (`.env.example`). The rule for sizing: worker processes × pool size must stay below
Postgres' `max_connections`. Past that point the next step is PgBouncer in front of Postgres,
or async routes to hold more waiting requests per process; neither was needed here.

Pool connections do not use server-side prepared statements: a cached generic plan cannot tell
a rare country from a common one and went back to scanning the whole table (180 ms).

## Lint exceptions

- `# noqa: E402` on the two `app` imports in `scripts/load_data.py`: `python scripts/load_data.py`
  puts `scripts/` on `sys.path`, not the repository root, so the root is added before those
  imports. The alternative was changing the `make load` contract.

No `type: ignore`.
