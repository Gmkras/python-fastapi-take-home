# Implementation Plan: API de consulta de registros Coffee Health

**Branch**: `002-records-api` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-records-api/spec.md`

## Summary

Cuatro rutas `GET` de solo lectura sobre los datos que deja la feature 001: listado de registros
con filtros y paginación por cursor sobre el ID, resumen por grupo, listado de filas no cargadas
y su conteo por motivo. Los parámetros de cada ruta son un modelo Pydantic que valida, normaliza
y documenta; una función pura convierte los filtros en un `WHERE` parametrizado. Un solo índice
nuevo, `(country, id)`. El pool de conexiones queda acotado y con límites de tiempo, y la
saturación se responde con un 503 en vez de dejar la petición colgada.

Medido en la investigación con 1 millón de filas: una página profunda por cursor tarda 0,06 ms
frente a 122 ms con `OFFSET`; un país inexistente, 0,09 ms con el índice frente a 110 ms sin él.

## Technical Context

**Language/Version**: Python 3.13 (`.python-version`; el proyecto exige ≥ 3.12)

**Primary Dependencies**: FastAPI 0.162, Pydantic 2.14, pydantic-settings, psycopg 3.3 (binary)
con psycopg-pool 3.3. Ya instaladas; ninguna nueva.

**Storage**: PostgreSQL 17 (Docker, puerto 5433). Tablas de la feature 001, sin cambios; un
índice nuevo en `db/init/003_indexes.sql`.

**Testing**: pytest 9 (filtros sin base de datos; rutas con `TestClient` contra la base real),
ruff, mypy strict

**Target Platform**: servicio web local (`make dev`) contra Postgres en Docker

**Project Type**: servicio web (repo único); la documentación interactiva es la interfaz

**Performance Goals**: con 1 millón de registros, la página más profunda tarda como máximo el
doble que la primera y ambas menos de 200 ms, sin filtros y filtrando por un país (SC-003)

**Constraints**: nunca más de 200 elementos por respuesta; solo lectura; error temporal en 6 s
como máximo (SC-008); sin dependencias nuevas ni ORM; contrato del Makefile intacto

**Scale/Scope**: 9.984 registros hoy; diseño para decenas de millones. 4 rutas nuevas

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Cómo lo cumple el diseño | Estado |
|---|---|---|
| I. Correctitud de datos | La API solo lee lo que la carga validó; expone las filas no cargadas con su motivo; los conteos del resumen suman el total (grupo "sin dato") | ✅ |
| II. Lógica pura y testeable | Validación de parámetros, `build_where` y `build_reject_where` en `app/filters.py`, sin I/O; `tests/test_filters.py` pasa sin base y se escribe antes que la implementación | ✅ |
| III. Idempotencia y atomicidad | No aplica a la escritura: la feature es de solo lectura. El índice se crea con `make db-reset` | ✅ |
| IV. Escala sin especular | Paginación por cursor (medida frente a `OFFSET`); un solo índice, justificado con una consulta y con su costo de escritura (R7); peor caso medido y documentado (R9); nada construido para el resumen a gran escala | ✅ |
| V. Simplicidad | Stack del repo, SQL explícito con `psycopg.sql`, sin ORM ni dependencias nuevas, Makefile intacto; tres módulos nuevos y sin capa de servicios | ✅ |
| VI. Calidad verificable | Tipos en todo; ruff y mypy strict; sin excepciones de lint previstas | ✅ |
| VII. Decisiones documentadas | 5 clarificaciones en la spec; 15 decisiones con alternativas y mediciones en research.md; resumen en NOTES.md al cerrar | ✅ |

**Re-check post-diseño**: sin violaciones. Dos diferencias respecto al texto de entrada del
`/speckit-plan`, las dos impuestas por la spec y comprobadas en prototipo:

1. `min ≤ max` se valida con un validador de campo en cada `max_*`, no con un
   `model_validator`: este no se ejecuta si otro parámetro ya falló, lo que incumple FR-018
   (R4).
2. Los filtros de categoría siguen tipados con los `StrEnum` compartidos, pero pasan antes por
   un normalizador, porque la clarificación Q3 pide aceptar `female` (R5).

Y una adición: las conexiones del pool no usan sentencias preparadas en el servidor (R8).

## Project Structure

### Documentation (this feature)

```text
specs/002-records-api/
├── spec.md
├── plan.md              # este archivo
├── research.md          # R1–R15
├── data-model.md        # índice, modelos de respuesta y de entrada, configuración
├── quickstart.md        # validación de punta a punta
├── contracts/
│   └── api.md           # rutas, parámetros, respuestas, errores
├── checklists/
│   └── requirements.md
└── tasks.md             # lo genera /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── config.py            # MODIFICAR: tamaño de pool y límites de tiempo
├── db.py                # MODIFICAR: pool desde Settings, statement_timeout, prepare_threshold
├── main.py              # MODIFICAR: incluir el router, traducir errores a 503
├── validation.py        # existente; se reutilizan StrEnum, COUNTRIES y RejectReason
├── schemas.py           # NUEVO: Record, RecordPage, SummaryRow, Reject, RejectPage, ReasonCount
├── filters.py           # NUEVO: modelos de parámetros, normalización, build_where,
│                        #        build_reject_where, GROUP_BY
└── records.py           # NUEVO: APIRouter con las 4 rutas y su SQL

db/init/
├── 001_notes.sql        # existente
├── 002_health_records.sql  # existente
└── 003_indexes.sql      # NUEVO: índice (country, id)

tests/
├── conftest.py          # MODIFICAR: fixture que inserta y borra filas de prueba
├── test_filters.py      # NUEVO: sin base de datos
└── test_records_api.py  # NUEVO: rutas contra Postgres

.env.example             # MODIFICAR: los cuatro ajustes nuevos, comentados
```

**Structure Decision**: se respeta la estructura plana de `app/`. Tres módulos nuevos con una
responsabilidad cada uno: qué se devuelve (`schemas.py`), qué se acepta y cómo se convierte en
SQL (`filters.py`, puro) y las rutas (`records.py`, el único que toca la base). No hay capa de
servicios ni de repositorio: son cuatro consultas de lectura.

## Diseño de las rutas (resumen; detalle en research y contracts/api.md)

```text
GET /records            params: RecordListParams
  where, params = build_where(filters)
  SELECT <16 columnas> FROM health_records
   WHERE id > %s [AND <where>] ORDER BY id LIMIT %s        -- limit + 1
  si llegan limit + 1 filas: se descarta la última y next_after_id = id del último devuelto
  si no: next_after_id = null

GET /records/summary    params: SummaryParams
  SELECT <GROUP_BY[group_by]> AS key, count(*), round(avg(sleep_hours), 2),
         round(avg(coffee_intake), 2), round(avg(bmi), 2)
    FROM health_records [WHERE <where>] GROUP BY 1 ORDER BY 1 NULLS LAST

GET /rejects            params: RejectListParams
  where, params = build_reject_where(after=..., reasons=..., source_file=...)
     -- condiciones posibles: (source_file, line_number) > (%s, %s) · reason = ANY(%s) · source_file = %s
  SELECT source_file, line_number, reason, column_name, raw_value, detail FROM load_rejects
   [WHERE <where>] ORDER BY source_file, line_number LIMIT %s      -- limit + 1

GET /rejects/summary    params: RejectSummaryParams
  where, params = build_reject_where(source_file=...)
  SELECT reason, count(*) FROM load_rejects [WHERE <where>]
   GROUP BY reason ORDER BY count(*) DESC, reason
```

Todas las condiciones salen de las dos funciones puras de `app/filters.py`; `app/records.py`
solo las coloca en una plantilla fija y ejecuta (constitución II).

Conexiones (`app/db.py`): `ConnectionPool` con `min_size`, `max_size` y `timeout` desde
`Settings`; cada conexión se abre con `options="-c statement_timeout=<ms>"` y
`prepare_threshold=None`, y el pool la comprueba antes de entregarla
(`check=ConnectionPool.check_connection`, research R11). En `app/main.py`, un manejador traduce `PoolTimeout`, `QueryCanceled`
y `OperationalError` a 503.

## Estrategia de pruebas

| Archivo | Base de datos | Cubre |
|---|---|---|
| `tests/test_filters.py` | No | Valores por defecto y límites de `limit` y `after_id`; cada caso de 422 de contracts/api.md; varios errores en una respuesta; `max < min` nombrando el parámetro y junto a otros errores; normalización de país y categorías (`germany`, ` SPAIN `, `female`); país desconocido conservado; `build_where`: SQL y parámetros de cada filtro, combinación con `AND`, sin filtros → vacío; `GROUP_BY` cubre los cinco valores; cursor de filas no cargadas, los dos o ninguno; `build_reject_where`: SQL y parámetros de cada condición |
| `tests/test_records_api.py` | Sí | Recorrido completo sin repetidos ni huecos; última página exactamente llena → cursor nulo; cursor más allá del final; cada filtro y sus combinaciones; filtro que excluye los registros sin dato; números como números y faltantes como `null`; 422 por HTTP; resumen: conteos que suman el listado, grupo `null`, promedio `null`, tramos de tazas; filas no cargadas: orden, cursor, filtros y conteo; 503 con el pool agotado y con una consulta que supera el límite; `/health` y `/notes` intactos; la documentación lista los parámetros y sus valores admitidos; las rutas nuevas solo exponen `GET` |

Las pruebas de integración insertan y confirman filas propias (IDs ≥ 9.000.000, país `Testland`)
y las borran al terminar; no dependen de los datos cargados (R14).

## Complexity Tracking

Sin violaciones de la constitución que justificar.
