# Notes

Draft covering the loader. The API sections (filtering, pagination, API scale) are added with
that feature. The full reasoning behind each decision is in `specs/001-csv-loader/`.

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
- Indexes: only the two primary keys so far. Indexes for filters are chosen with the API,
  each one justified by a query plan.

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
grows linearly: 10 million rows would take roughly 8 to 9 minutes.

Trade-offs and what I would change for tens of millions of rows:

- One transaction keeps a failed load from leaving anything behind, at the cost of a long
  transaction and a staging table about the size of the file. For very large initial loads I
  would commit per chunk and keep a table of runs to resume from.
- Validation is single-process. Splitting it across processes is the next step if throughput
  matters; it complicates line numbering and atomicity, so it was left out.
- Secondary indexes should be created after a bulk load, not maintained row by row during it.

## Lint exceptions

- `# noqa: E402` on the two `app` imports in `scripts/load_data.py`: `python scripts/load_data.py`
  puts `scripts/` on `sys.path`, not the repository root, so the root is added before those
  imports. The alternative was changing the `make load` contract.

No `type: ignore`.
