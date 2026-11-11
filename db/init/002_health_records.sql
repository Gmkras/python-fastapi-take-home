-- Coffee health dataset.
--
-- health_records: one row per person. The source ID is the primary key, which is what makes
-- the loader idempotent (re-loading a file updates by ID instead of inserting again).
--
-- Design notes:
--   * numeric(n,1), not real: the source has one decimal place and exact values keep range
--     filters and averages free of binary floating point noise.
--   * text + CHECK, not native ENUM types: same guarantee, simpler to evolve with ALTER TABLE.
--   * Only id, country, coffee_intake and caffeine_mg are NOT NULL. The other columns may be
--     missing in the source and are stored as NULL; a CHECK passes on NULL, so the range and
--     domain rules below apply only when a value is present.
--   * country has no catalog CHECK: the catalog is open-ended and enforced by the loader.
--   * The CHECKs mirror app/validation.py and are a second barrier, not the first.
create table public.health_records (
  id                      integer primary key check (id >= 1),
  age                     smallint check (age between 18 and 120),
  gender                  text check (gender in ('Male', 'Female', 'Other')),
  country                 text not null check (btrim(country) <> ''),
  coffee_intake           numeric(5,1) not null check (coffee_intake between 0 and 20),
  caffeine_mg             numeric(6,1) not null check (caffeine_mg between 0 and 2000),
  sleep_hours             numeric(5,1) check (sleep_hours between 1 and 16),
  sleep_quality           text check (sleep_quality in ('Poor', 'Fair', 'Good', 'Excellent')),
  bmi                     numeric(5,1) check (bmi between 10 and 80),
  heart_rate              smallint check (heart_rate between 30 and 200),
  stress_level            text check (stress_level in ('Low', 'Medium', 'High')),
  physical_activity_hours numeric(5,1) check (physical_activity_hours between 0 and 80),
  health_issues           text check (health_issues in ('None', 'Mild', 'Moderate', 'Severe')),
  occupation              text
    check (occupation in ('Office', 'Healthcare', 'Service', 'Student', 'Other')),
  smoking                 boolean,
  alcohol_consumption     boolean,
  loaded_at               timestamptz not null default now()
);

-- load_rejects: every source line that did not become a record, with the reason.
-- Holds only the latest load of each file: the loader deletes a file's rows and inserts the
-- new ones in the same transaction, so re-loading never duplicates them.
create table public.load_rejects (
  source_file text not null,
  line_number integer not null,
  reason      text not null check (reason in (
    'column_count', 'missing_required', 'not_numeric', 'out_of_range', 'unknown_category',
    'unknown_country', 'invalid_boolean', 'duplicate_exact', 'duplicate_conflict'
  )),
  column_name text,
  raw_value   text,
  detail      text not null,
  raw_fields  text[] not null,
  loaded_at   timestamptz not null default now(),
  primary key (source_file, line_number)
);
