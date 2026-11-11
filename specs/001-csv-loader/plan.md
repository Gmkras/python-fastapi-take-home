# Implementation Plan: Cargador del dataset Coffee Health

**Branch**: `001-csv-loader` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-csv-loader/spec.md`

## Summary

Un CLI en Python (`scripts/load_data.py`) lee el CSV sucio en streaming. Valida y normaliza
cada fila con un modelo Pydantic de reglas estrictas (`app/validation.py`, sin I/O). Envía las
filas válidas por `COPY` a una tabla temporal y los rechazos a la tabla `load_rejects`. Resuelve
los IDs duplicados en SQL ("gana la última") y aplica los ganadores con un `MERGE` que solo
escribe lo que cambió. Todo ocurre en una transacción con verificación de invariantes antes del
commit.

Resultado esperado con el archivo actual: 9.984 registros (9 con datos opcionales vacíos) y 28
filas no cargadas explicadas (16 rechazos y 12 duplicados).

## Technical Context

**Language/Version**: Python 3.13 (`.python-version`; el proyecto exige ≥ 3.12)

**Primary Dependencies**: Pydantic 2.14, pydantic-settings, psycopg 3.3 (binary). Ya instaladas;
ninguna nueva.

**Storage**: PostgreSQL 17 (Docker, puerto 5433). Esquema en `db/init/002_health_records.sql`.

**Testing**: pytest 9 (validación sin base de datos; integración contra la base real), ruff,
mypy strict

**Target Platform**: proceso CLI local (Windows, macOS o Linux) contra Postgres en Docker

**Project Type**: CLI de ingesta por lotes dentro de un servicio web existente (repo único)

**Performance Goals**: archivo actual en menos de 10 s (SC-005); al menos 20.000 filas/s de
extremo a extremo con 1 millón de filas (SC-006). La validación sola mide 51.600 filas/s
(research R1).

**Constraints**: memoria independiente del tamaño del archivo (FR-028); una transacción (FR-023);
una carga a la vez (R10); contrato del Makefile intacto

**Scale/Scope**: 10.012 filas hoy; diseño para decenas de millones

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Cómo lo cumple el diseño | Estado |
|---|---|---|
| I. Correctitud de datos | Validación estricta antes de la base; `load_rejects` con línea y motivo para cada fila no cargada; invariantes verificados antes del commit; `CHECK` en la base como segunda barrera | ✅ |
| II. Lógica pura y testeable | `app/validation.py` sin I/O; `tests/test_validation.py` sin base de datos y escritos antes de implementar | ✅ |
| III. Idempotencia y atomicidad | `MERGE ... IS DISTINCT FROM`; rechazos reemplazados por archivo; una transacción; `pg_advisory_xact_lock` | ✅ |
| IV. Escala sin especular | Streaming por bloques; `COPY`; deduplicación y upsert set-based en Postgres; solo índices PK; rendimiento medido (R1, R15) | ✅ |
| V. Simplicidad | Stack del repo, sin ORM ni dependencias nuevas, Makefile intacto, sin capas extra | ✅ |
| VI. Calidad verificable | Tipos en todo; ruff y mypy strict; única excepción prevista `# noqa: E402` (R11), justificada y listada en NOTES.md | ✅ con excepción documentada |
| VII. Decisiones documentadas | Clarificaciones en la spec; alternativas en research.md; resumen en NOTES.md | ✅ |

**Re-check post-diseño**: sin cambios; data-model, contrato y quickstart respetan los 7
principios. La única desviación respecto a la entrada del `/speckit-plan` ("todo NOT NULL") la
impone la clarificación Q5 de la spec, no es una violación.

## Project Structure

### Documentation (this feature)

```text
specs/001-csv-loader/
├── spec.md
├── plan.md              # este archivo
├── research.md          # R1–R15
├── data-model.md        # health_records, load_rejects, _staging, objetos Python
├── quickstart.md        # validación de punta a punta
├── contracts/
│   └── cli.md           # argumentos, salida, errores, consulta de rechazos
├── checklists/
│   └── requirements.md
└── tasks.md             # lo genera /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── config.py            # existente; sin cambios
├── db.py                # existente; sin cambios
├── main.py              # existente; sin cambios en esta feature
└── validation.py        # NUEVO: catálogos, límites, StrEnum, HealthRecord, RejectReason, parse_row

scripts/
├── load_data.py         # IMPLEMENTAR: CLI, load_csv(), resumen, invariantes
└── make_big_csv.py      # NUEVO (apoyo): genera CSV grandes para medir escala

db/init/
├── 001_notes.sql        # existente; se conserva
└── 002_health_records.sql  # NUEVO: health_records + load_rejects + CHECK

tests/
├── conftest.py          # existente; se añade el fixture db_conn (conexión + rollback)
├── test_health.py       # existente; sin cambios
├── test_validation.py   # NUEVO: sin base de datos
├── test_load_data.py    # NUEVO: integración contra Postgres
└── data/                # NUEVO: CSV pequeños de prueba (IDs ≥ 9.000.000)
```

**Structure Decision**: se respeta la estructura del repo. El núcleo puro va en `app/` porque
la feature 002 (API) reutilizará los enums y catálogos. La orquestación de base de datos
queda en `scripts/load_data.py`, que es lo que ejecuta `make load`, y se prueba importando
`scripts.load_data`.

## Diseño de `load_csv` (resumen; detalle en research R6–R10)

```text
main(): argparse → connect(timeout=10) → load_csv(conn, path, chunk_size) → print summary → commit
load_csv (sin commit):
  1. pg_advisory_xact_lock
  2. leer cabecera → error si ≠ EXPECTED_HEADER
  3. DELETE FROM load_rejects WHERE source_file = name
  4. CREATE TEMP TABLE _staging ... ON COMMIT DROP
  5. por cada bloque de chunk_size filas:
       parse_row → válidas | rechazos
       COPY válidas → _staging ; COPY rechazos → load_rejects
  6. INSERT INTO load_rejects ... perdedoras de row_number() (exacto | conflicto)
  7. MERGE ganadoras → health_records RETURNING merge_action()
  8. contar en la base y verificar invariantes (data-model §6) → error si no cuadran
  9. devolver LoadSummary
```

## Estrategia de pruebas

| Archivo | Base de datos | Cubre |
|---|---|---|
| `tests/test_validation.py` | No | Cada parser y normalizador, límites inclusivos, faltantes en obligatorias y opcionales, `fixed`, mapeo de errores a `RejectReason`, caso de varias columnas inválidas, cabecera. Caracterización: recorrer el CSV real da 16 rechazos con su desglose, 9.996 válidas, 56 corregidas y 9 con faltantes |
| `tests/test_load_data.py` | Sí (rollback) | Conteos y conciliación; idempotencia; "gana la última"; exacto frente a conflicto; recarga con cambio (1 actualizada); fila inválida no toca un registro existente; rechazos de otro archivo intactos; `chunk_size=3` igual que un bloque, con duplicados entre bloques; BOM/CRLF frente a LF; `CHECK` rechaza inserciones directas fuera de rango; cabecera inválida no cambia nada |

Las pruebas de integración usan IDs ≥ 9.000.000 y archivos `test_*.csv` para no chocar con
los datos reales, y se deshacen al terminar.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `# noqa: E402` en `scripts/load_data.py` | `make load` ejecuta el script por ruta y `app` no está en `sys.path` | Cambiar el Makefile o convertir el repo en paquete instalable rompe el contrato del repo (constitución V) |
