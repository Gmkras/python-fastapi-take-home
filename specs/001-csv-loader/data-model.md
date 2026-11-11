# Data Model: Cargador del dataset Coffee Health

**Feature**: 001-csv-loader | **Date**: 2026-11-11 | Decisiones en [research.md](research.md)

## 1. `health_records` — un registro por persona

| Columna | Tipo | Nulo | Regla (validador = `CHECK`) | CSV de origen |
|---|---|---|---|---|
| `id` | `integer` PK | no | 1 – 2.147.483.647 | `ID` |
| `age` | `smallint` | sí | 18 – 120 | `Age` |
| `gender` | `text` | sí | `Male`, `Female`, `Other` | `Gender` |
| `country` | `text` | no | no vacío; catálogo en el loader | `Country` |
| `coffee_intake` | `numeric(5,1)` | no | 0 – 20 | `Coffee_Intake` |
| `caffeine_mg` | `numeric(6,1)` | no | 0 – 2000 | `Caffeine_mg` |
| `sleep_hours` | `numeric(5,1)` | sí | 1 – 16 | `Sleep_Hours` |
| `sleep_quality` | `text` | sí | `Poor`, `Fair`, `Good`, `Excellent` | `Sleep_Quality` |
| `bmi` | `numeric(5,1)` | sí | 10 – 80 | `BMI` |
| `heart_rate` | `smallint` | sí | 30 – 200 | `Heart_Rate` |
| `stress_level` | `text` | sí | `Low`, `Medium`, `High` | `Stress_Level` |
| `physical_activity_hours` | `numeric(5,1)` | sí | 0 – 80 | `Physical_Activity_Hours` |
| `health_issues` | `text` | sí | `None`, `Mild`, `Moderate`, `Severe` | `Health_Issues` |
| `occupation` | `text` | sí | `Office`, `Healthcare`, `Service`, `Student`, `Other` | `Occupation` |
| `smoking` | `boolean` | sí | — | `Smoking` |
| `alcohol_consumption` | `boolean` | sí | — | `Alcohol_Consumption` |
| `loaded_at` | `timestamptz` | no | `DEFAULT now()`; se actualiza en cada `UPDATE` | — |

- Obligatorias (Q5): `id`, `country`, `coffee_intake`, `caffeine_mg`.
- `health_issues = 'None'` es un valor del dominio, distinto de `NULL` (dato ausente).
- Catálogo de países (20): Australia, Belgium, Brazil, Canada, China, Finland, France, Germany,
  India, Italy, Japan, Mexico, Netherlands, Norway, South Korea, Spain, Sweden, Switzerland,
  UK, USA.
- Índices: solo la PK (R14).

## 2. `load_rejects` — filas del archivo que no llegaron a ser registro

| Columna | Tipo | Nulo | Descripción |
|---|---|---|---|
| `source_file` | `text` | no | Nombre del archivo (`Path.name`) |
| `line_number` | `integer` | no | Línea física en el archivo (cabecera = 1) |
| `reason` | `text` | no | `CHECK` contra los códigos de R2 |
| `column_name` | `text` | sí | Columna CSV de la primera causa (nulo en `column_count` y duplicados) |
| `raw_value` | `text` | sí | Valor original sin recortar de esa columna |
| `detail` | `text` | no | Texto legible: todas las columnas inválidas, o "superseded by line N" |
| `raw_fields` | `text[]` | no | Los campos originales de la fila, tal como los dio el parser CSV |
| `loaded_at` | `timestamptz` | no | `DEFAULT now()` |

- `PRIMARY KEY (source_file, line_number)`: una fila del archivo aparece como máximo una vez.
- Ciclo de vida: al cargar un archivo, `DELETE ... WHERE source_file = $1` y luego se insertan
  sus rechazos, dentro de la misma transacción (FR-021, Q4). Las filas de otros archivos no se
  tocan.

## 3. `_staging` — tabla temporal de la carga

`DROP TABLE IF EXISTS _staging`, y luego `CREATE TEMP TABLE _staging (line_no integer NOT NULL,
<las 16 columnas de health_records sin loaded_at>, raw_fields text[] NOT NULL) ON COMMIT DROP`.

- El `DROP ... IF EXISTS` permite dos cargas dentro de una misma transacción (así corren los
  tests de idempotencia).
- `raw_fields` guarda los campos originales de la fila: si resulta ser un duplicado descartado,
  `load_rejects` conserva la fila original (FR-020).
- Recibe solo filas válidas, por `COPY`.
- Sin `CHECK` ni PK: el validador ya filtró y los IDs pueden repetirse.
- Desaparece al confirmar o deshacer la transacción.

## 4. Objetos en Python (`app/validation.py`, sin I/O)

| Objeto | Tipo | Contenido |
|---|---|---|
| `Gender`, `SleepQuality`, `StressLevel`, `HealthIssues`, `Occupation` | `StrEnum` | Valores canónicos |
| `COUNTRIES` | `frozenset[str]` | Catálogo de 20 países |
| `LIMITS` | constantes | Rangos de FR-014, una sola fuente para validador, SQL y tests |
| `MISSING_MARKERS` | `frozenset[str]` | `""`, `n/a`, `na`, `?`, `unknown`, `null` (comparados con `casefold`) |
| `EXPECTED_HEADER` | `tuple[str, ...]` | Las 16 columnas en orden |
| `HealthRecord` | `BaseModel` (frozen) | 16 campos tipados; obligatorios sin `None` |
| `RejectReason` | `StrEnum` | Códigos de R2 |
| `Rejection` | `dataclass(frozen)` | `line_number`, `reason`, `column_name`, `raw_value`, `detail`, `raw_fields` |
| `ParsedRow` | `dataclass(frozen)` | `line_number`, `record`, `fixed: frozenset[str]`, `missing: frozenset[str]` |
| `parse_row(line_number, fields)` | función | `ParsedRow \| Rejection` |
| `check_header(fields)` | función | No devuelve nada; lanza `HeaderError` (con esperado y encontrado) si la cabecera no es `EXPECTED_HEADER` |

## 5. Flujo de una fila

```text
línea del CSV
  ├─ vacía ───────────────────────────► blank_lines += 1 (no se registra)
  ├─ len(fields) != 16 ───────────────► Rejection(column_count)
  └─ parse_row
       ├─ errores de validación ──────► Rejection(primer motivo, detalle con todos)
       └─ válida ─► _staging
                      ├─ no es la última aparición de su ID ─► load_rejects(duplicate_exact | duplicate_conflict)
                      └─ ganadora ─► MERGE ─► insertada | actualizada | sin cambios
```

## 6. Invariantes de una carga (FR-026)

- `read = rejected + duplicates + winners`
- `winners = inserted + updated + unchanged`
- `count(load_rejects WHERE source_file = f) = rejected + duplicates`
- `rows_in_table = rows_before + inserted` (`rows_before` se cuenta al empezar, ya con el bloqueo
  tomado; vale aunque la tabla tenga datos de otros archivos)
- `valid = read − rejected`; `fixed ≤ valid`; `with_missing ≤ valid` (`fixed` y `with_missing` se
  cuentan sobre las filas válidas, incluidas las duplicadas descartadas)

Valores esperados con `data/coffee_health_raw.csv`: `read` 10.012, `blank` 3, `rejected` 16,
`valid` 9.996, `duplicates` 12 (8 exactos y 4 en conflicto), `winners` 9.984, `fixed` 56,
`with_missing` 9, registros en `load_rejects` 28. En base vacía: 9.984 insertadas; en la
segunda carga: 9.984 sin cambios.
