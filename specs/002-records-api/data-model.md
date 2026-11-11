# Data Model: API de consulta de registros Coffee Health

**Feature**: 002-records-api | **Date**: 2026-11-11 | Decisiones en [research.md](research.md)

Esta feature no crea tablas ni cambia las existentes: lee `health_records` y `load_rejects`, tal
como las define la feature 001 ([data-model](../001-csv-loader/data-model.md)). Añade un índice
y los modelos de entrada y salida de la API.

## 1. Base de datos

`db/init/003_indexes.sql`:

| Índice | Tabla | Columnas | Sirve a | Costo |
|---|---|---|---|---|
| PK (ya existe) | `health_records` | `(id)` | Listado y cursor; filtros poco selectivos | — |
| nuevo | `health_records` | `(country, id)` | País con pocas filas o inexistente; resumen por país | +39 % en la inserción, 35 MB por 1,2 M de filas (R7) |
| PK (ya existe) | `load_rejects` | `(source_file, line_number)` | Listado y cursor de filas no cargadas | — |

Los scripts de `db/init/` solo se ejecutan al crear el volumen: aplicar el índice exige
`make db-reset` y, después, `make load`.

## 2. Modelos de respuesta (`app/schemas.py`)

### `Record` — un registro de salud

| Campo | Tipo JSON | Nulo | Origen |
|---|---|---|---|
| `id` | entero | no | `id` |
| `age` | entero | sí | `age` |
| `gender` | texto (`Male`, `Female`, `Other`) | sí | `gender` |
| `country` | texto | no | `country` |
| `coffee_intake` | número | no | `coffee_intake` |
| `caffeine_mg` | número | no | `caffeine_mg` |
| `sleep_hours` | número | sí | `sleep_hours` |
| `sleep_quality` | texto (`Poor`, `Fair`, `Good`, `Excellent`) | sí | `sleep_quality` |
| `bmi` | número | sí | `bmi` |
| `heart_rate` | entero | sí | `heart_rate` |
| `stress_level` | texto (`Low`, `Medium`, `High`) | sí | `stress_level` |
| `physical_activity_hours` | número | sí | `physical_activity_hours` |
| `health_issues` | texto (`None`, `Mild`, `Moderate`, `Severe`) | sí | `health_issues` |
| `occupation` | texto (`Office`, `Healthcare`, `Service`, `Student`, `Other`) | sí | `occupation` |
| `smoking` | booleano | sí | `smoking` |
| `alcohol_consumption` | booleano | sí | `alcohol_consumption` |

- Las columnas `numeric` se exponen como `float` (R10). Los categóricos reutilizan los `StrEnum`
  de `app/validation.py`.
- `health_issues = "None"` es el texto de un valor del dominio; un dato que falta es `null`.
- `loaded_at` no se expone (FR-019).

### Otros modelos

| Modelo | Campos |
|---|---|
| `RecordPage` | `items: list[Record]`, `next_after_id: int \| null`, `limit: int` |
| `SummaryRow` | `key: str \| int \| null`, `count: int`, `avg_sleep_hours`, `avg_coffee_intake`, `avg_bmi` (número o `null`) |
| `Reject` | `source_file`, `line_number`, `reason`, `column_name` (nulo), `raw_value` (nulo), `detail` |
| `RejectCursor` | `source_file: str`, `line_number: int` |
| `RejectPage` | `items: list[Reject]`, `next_after: RejectCursor \| null`, `limit: int` |
| `ReasonCount` | `reason`, `count` |

`SummaryRow.key` es texto al agrupar por país o categoría, entero al agrupar por tazas de café
y `null` para el grupo "sin dato" (R12).

## 3. Modelos de entrada (`app/filters.py`)

Todos con `extra="forbid"` (R3).

### `RecordFilters` — filtros comunes al listado y al resumen

| Parámetro | Tipo | Regla |
|---|---|---|
| `country` | lista de texto | Recorta y normaliza con el catálogo; lo desconocido no coincide con nada |
| `gender` | lista de `Gender` | Recorta, sin distinguir mayúsculas; fuera del catálogo → 422 |
| `sleep_quality` | lista de `SleepQuality` | Igual |
| `stress_level` | lista de `StressLevel` | Igual |
| `smoking` | booleano | — |
| `min_age`, `max_age` | entero | `max_age ≥ min_age` si vienen los dos |
| `min_coffee_intake`, `max_coffee_intake` | decimal | `max ≥ min` |
| `min_bmi`, `max_bmi` | decimal | `max ≥ min` |

Todos son opcionales. Cada `min_*` se declara antes que su `max_*` (R4).

### Modelos por endpoint

| Modelo | Añade a `RecordFilters` |
|---|---|
| `RecordListParams` | `limit: int = 50` (1–200), `after_id: int = 0` (≥ 0) |
| `SummaryParams` | `group_by` obligatorio: `country`, `gender`, `sleep_quality`, `stress_level` o `coffee_cups` |

| Modelo | Parámetros |
|---|---|
| `RejectListParams` | `limit` (1–200, por defecto 50); `after_source_file` y `after_line_number` (≥ 0), los dos o ninguno (si falta uno, el error se sitúa en `after_line_number`); `reason` (lista de `RejectReason`, normalizada como las categorías); `source_file` (nombre exacto) |
| `RejectSummaryParams` | `source_file` opcional |

### Funciones puras

| Función | Devuelve |
|---|---|
| `build_where(filters)` | `(sql.Composable, list[params])`: las condiciones de los filtros presentes unidas con `AND`; SQL vacío sin filtros (R6) |
| `build_reject_where(after, reasons, source_file)` | `(sql.Composable, list[params])` para `load_rejects`: `(source_file, line_number) > (%s, %s)`, `reason = ANY(%s)` y `source_file = %s`, solo las presentes (R13) |
| `GROUP_BY` | Diccionario fijo de `group_by` → expresión SQL (R12) |

Traducción de filtro a condición:

| Filtro | Condición |
|---|---|
| `country`, `gender`, `sleep_quality`, `stress_level` | `columna = ANY(%s)` |
| `smoking` | `smoking = %s` |
| `min_x` / `max_x` | `x >= %s` / `x <= %s` |

Ninguna condición coincide con `NULL`, así que un filtro excluye por sí solo los registros sin
ese dato (FR-013): no hace falta tratarlo aparte.

## 4. Configuración (`app/config.py`)

| Ajuste | Variable de entorno | Defecto | Efecto |
|---|---|---|---|
| `db_pool_min_size` | `DB_POOL_MIN_SIZE` | 1 | Conexiones abiertas en reposo |
| `db_pool_max_size` | `DB_POOL_MAX_SIZE` | 10 | Máximo de conexiones del proceso |
| `db_pool_timeout_seconds` | `DB_POOL_TIMEOUT_SECONDS` | 5 | Espera máxima por una conexión → 503 |
| `db_statement_timeout_ms` | `DB_STATEMENT_TIMEOUT_MS` | 5000 | Duración máxima de una consulta → 503 |

`database_url` no cambia.
