---

description: "Task list for feature 001-csv-loader"
---

# Tasks: Cargador del dataset Coffee Health

**Input**: Design documents from `specs/001-csv-loader/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Incluidas. La constitución (principio II) exige TDD para la lógica pura y la prueba
pide tests de validación sin base de datos. Las tareas de test van antes que su implementación y
deben fallar primero.

**Organization**: Tareas agrupadas por historia de usuario (US1–US5 de spec.md).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Puede ejecutarse en paralelo (archivo distinto, sin dependencias pendientes)
- **[Story]**: Historia a la que pertenece (US1…US5)
- Rutas relativas a la raíz del repositorio

## Valores de referencia (data-model.md §6)

Con `data/coffee_health_raw.csv`: leídas 10.012 · en blanco 3 · rechazadas 16 (out_of_range 6,
not_numeric 5, column_count 3, invalid_boolean 2) · válidas 9.996 · corregidas 56 · con
faltantes 9 · duplicadas 12 (8 exactas, 4 en conflicto) · cargadas 9.984 · `load_rejects` 28.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dejar el entorno listo y confirmar la línea base.

- [X] T001 Verificar la línea base: `make db-up` y `make test` (2 passed) y `make lint` en verde, sin cambios de código; si `make lint` falla en el repo original, anotar el error exacto en specs/001-csv-loader/research.md antes de seguir
- [X] T002 [P] Crear el directorio de datos de prueba tests/data/ con un archivo tests/data/.gitkeep

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Esquema, catálogos y fixtures que usan todas las historias.

**⚠️ CRITICAL**: Ninguna historia empieza antes de terminar esta fase.

- [X] T003 Crear db/init/002_health_records.sql con la tabla `health_records` exactamente como data-model.md §1: `id integer PRIMARY KEY` con `CHECK (id BETWEEN 1 AND 2147483647)`; `age smallint` `CHECK (age BETWEEN 18 AND 120)`; `gender text` `CHECK (gender IN ('Male','Female','Other'))`; `country text NOT NULL` `CHECK (btrim(country) <> '')`; `coffee_intake numeric(5,1) NOT NULL` `CHECK (coffee_intake BETWEEN 0 AND 20)`; `caffeine_mg numeric(6,1) NOT NULL` `CHECK (caffeine_mg BETWEEN 0 AND 2000)`; `sleep_hours numeric(5,1)` `CHECK (sleep_hours BETWEEN 1 AND 16)`; `sleep_quality text` `CHECK (sleep_quality IN ('Poor','Fair','Good','Excellent'))`; `bmi numeric(5,1)` `CHECK (bmi BETWEEN 10 AND 80)`; `heart_rate smallint` `CHECK (heart_rate BETWEEN 30 AND 200)`; `stress_level text` `CHECK (stress_level IN ('Low','Medium','High'))`; `physical_activity_hours numeric(5,1)` `CHECK (physical_activity_hours BETWEEN 0 AND 80)`; `health_issues text` `CHECK (health_issues IN ('None','Mild','Moderate','Severe'))`; `occupation text` `CHECK (occupation IN ('Office','Healthcare','Service','Student','Other'))`; `smoking boolean`; `alcohol_consumption boolean`; `loaded_at timestamptz NOT NULL DEFAULT now()`. Solo `id`, `country`, `coffee_intake`, `caffeine_mg` y `loaded_at` son NOT NULL. Comentario SQL breve por decisión no obvia (numeric, text+CHECK, nulos)
- [X] T004 En el mismo db/init/002_health_records.sql añadir `load_rejects` como data-model.md §2: `source_file text NOT NULL`, `line_number integer NOT NULL`, `reason text NOT NULL CHECK (reason IN ('column_count','missing_required','not_numeric','out_of_range','unknown_category','unknown_country','invalid_boolean','duplicate_exact','duplicate_conflict'))`, `column_name text`, `raw_value text`, `detail text NOT NULL`, `raw_fields text[] NOT NULL`, `loaded_at timestamptz NOT NULL DEFAULT now()`, `PRIMARY KEY (source_file, line_number)`. Mantener db/init/001_notes.sql sin cambios. Aplicar con `make db-reset` y comprobar con `\d health_records` y `\d load_rejects` en `make db-shell`
- [X] T005 [P] Crear app/validation.py con las constantes y tipos sin lógica (data-model.md §4): `EXPECTED_HEADER` (las 16 columnas en el orden de FR-002), `StrEnum` `Gender`, `SleepQuality`, `StressLevel`, `HealthIssues`, `Occupation`, `RejectReason` (los 9 códigos de research R2), `COUNTRIES` (frozenset de los 20 países de data-model.md §1), `MISSING_MARKERS = frozenset({"", "n/a", "na", "?", "unknown", "null"})` comparados con `casefold()`, `TRUE_TOKENS = {"1","y","yes","true"}`, `FALSE_TOKENS = {"0","n","no","false"}` y límites de FR-014 como constantes (`AGE = (18, 120)`, etc.), una sola fuente para validador y tests
- [X] T006 [P] Añadir en tests/conftest.py el fixture `db_conn` (scope function): abre `psycopg.connect(settings.database_url, connect_timeout=10)`, lo cede sin autocommit y hace `rollback()` y `close()` al final, para que las pruebas de integración no dejen datos. Conservar el fixture `client` existente

**Checkpoint**: `make db-reset` crea ambas tablas; `uv run mypy` pasa con app/validation.py.

---

## Phase 3: User Story 1 - Cargar solo registros válidos y normalizados (Priority: P1) 🎯 MVP

**Goal**: La carga deja en `health_records` un registro por ID, normalizado y válido.

**Independent Test**: `make db-reset && make load` → `select count(*) from health_records` = 9.984,
con 9 registros con algún opcional vacío y solo valores canónicos.

### Tests for User Story 1 (escribir primero; deben fallar) ⚠️

- [X] T007 [US1] Crear tests/test_validation.py (sin base de datos, `pytest.mark.parametrize`) con los casos de parseo y normalización: enteros `'40'`→40 y `' 40 '`→40 con `fixed`; rechazo `not_numeric` de `'4O'`, `'thirty'`, `'-3'`, `'+40'`, `'40.0'`, `'4_0'`, `'1e2'`, `'４０'`; decimales `'3.5'`→`Decimal('3.5')` (tipo `Decimal`, nunca `float`), `'3'` válido sin `fixed`, `'3.14'`→`3.1` y `'3.15'`→`3.2` con `fixed`; rechazo `not_numeric` de `'3,5'`, `'.5'`, `'5.'`, `'NaN'`, `'Infinity'`, `'1e3'`, `'seven'`; límites inclusivos de cada rango (Age 17✗ 18✓ 120✓ 121✗; Heart_Rate 29✗ 30✓ 200✓ 201✗; Sleep_Hours 0.9✗ 1.0✓ 16.0✓ 16.1✗; BMI 9.9✗ 10.0✓ 80.0✓ 80.1✗; Coffee_Intake 20.0✓ 20.1✗; Caffeine_mg 2000.0✓ 2000.1✗; Physical_Activity_Hours 80.0✓ 80.1✗) con motivo `out_of_range`; `'9.96'` en BMI redondea a 10.0 y es válido
- [X] T008 [US1] En tests/test_validation.py añadir casos de categóricos, países y booleanos: `'  Female  '`, `'\tMale  '`, `'MALE'`, `'GOOD'`, `' Good '`, `'low'` → canónico con `fixed`; valor exacto sin `fixed`; `Health_Issues='None'` válido y no faltante; todos los valores de cada `StrEnum` aceptados; `'usa'`→`'USA'`, `'uk'`→`'UK'`, `'south korea'`→`'South Korea'`, `'SWEDEN'`→`'Sweden'`; `'Atlantis'`→`unknown_country`; booleanos `'0'`/`'1'` sin `fixed`, `'Y'`, `'yes'`, `'true'`, `'N'`, `'no'`, `'false'` con `fixed`; rechazo `invalid_boolean` de `'-1'`, `'2'`, `'maybe'`, `'on'`, `'off'`, `'t'`, `'f'`
- [X] T009 [US1] En tests/test_validation.py añadir casos de faltantes (Q5): `''`, `'n/a'`, `'N/A'`, `'?'`, `'unknown'` en Age, Gender, Sleep_Hours, BMI y Heart_Rate → fila válida con `None` y la columna en `missing`; los mismos valores en ID, Country, Coffee_Intake o Caffeine_mg → `missing_required`; `'thirty'` en una columna opcional sigue siendo `not_numeric` (FR-012a); `parse_row` con una fila completa válida devuelve `ParsedRow` con `fixed` vacío; `parse_row` no modifica la lista de entrada; `check_header` acepta `EXPECTED_HEADER` y lanza `HeaderError` (con esperado y encontrado en el mensaje) si falta una columna, sobra una o cambia el orden

### Implementation for User Story 1

- [X] T010 [US1] Implementar en app/validation.py los validadores previos (research R1): `_clean(value, info)` recorta, registra `info.field_name` en `info.context["fixed"]` si cambió y devuelve `None` si es marcador de faltante; `parse_int` con `re.fullmatch(r"\d+", s)` (no `str.isdigit()`); `parse_decimal` con `re.fullmatch(r"\d+(\.\d+)?", s)`, `Decimal` y `quantize(Decimal("0.1"), ROUND_HALF_UP)` marcando `fixed` si cambia; `parse_category(enum)` y `parse_country` sin distinguir mayúsculas; `parse_bool` con `TRUE_TOKENS`/`FALSE_TOKENS`. Errores con `PydanticCustomError` usando los códigos de `RejectReason`; variantes "required" que lanzan `missing_required` en vez de devolver `None`
- [X] T011 [US1] Definir en app/validation.py `HealthRecord(BaseModel)` frozen con los 16 campos en snake_case. Las restricciones van DENTRO del opcional, nunca sobre la unión (research R1 hallazgo 1): `Annotated[Annotated[Decimal, Field(ge=10, le=80)] | None, BeforeValidator(parse_decimal)]`; obligatorios (`id`, `country`, `coffee_intake`, `caffeine_mg`) sin `| None`. Rangos desde las constantes de T005
- [X] T012 [US1] Implementar en app/validation.py `ParsedRow` (dataclass frozen: `line_number`, `record`, `fixed: frozenset[str]`, `missing: frozenset[str]`) y `parse_row(line_number, fields) -> ParsedRow | Rejection` que mapea los 16 campos a `HealthRecord.model_validate(data, context={"fixed": set()})`; mapear `greater_than_equal`/`less_than_equal` → `out_of_range`; hasta que exista la historia 2 basta con un `Rejection` mínimo (`line_number`, `reason`). Añadir también `HeaderError(ValueError)` y la función pura `check_header(fields) -> None`, que compara con `EXPECTED_HEADER` y lanza `HeaderError` con lo esperado y lo encontrado. Hacer pasar T007–T009 con `uv run pytest tests/test_validation.py`
- [X] T013 [US1] Implementar en scripts/load_data.py el arranque (research R11, R12): `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` seguido de los imports de `app` con `# noqa: E402`; argparse con `csv_path` y `--chunk-size` (int ≥ 1, defecto 50000); conexión `psycopg.connect(settings.database_url, connect_timeout=10)`; `main(argv: list[str] | None = None) -> int` llama a `load_csv(conn, path, chunk_size)`, hace `commit()` y devuelve el código de salida (para poder probarlo); al final del archivo, `if __name__ == "__main__": raise SystemExit(main())`
- [X] T014 [US1] Implementar en scripts/load_data.py la lectura: `open(path, encoding="utf-8-sig", newline="")` + `csv.reader`; validar la cabecera con `check_header` de app/validation.py (error fatal con esperado y encontrado, FR-002); recorrer filas usando `reader.line_num` como número de línea; contar y omitir líneas en blanco; acumular bloques de `chunk_size` filas parseadas con `parse_row`
- [X] T015 [US1] Implementar en scripts/load_data.py la escritura de válidas (research R6, R7, R8): `DROP TABLE IF EXISTS _staging` y después `CREATE TEMP TABLE _staging (line_no integer NOT NULL, <16 columnas>, raw_fields text[] NOT NULL) ON COMMIT DROP` (el `DROP` permite dos cargas en una misma transacción, como hacen los tests; `raw_fields` son los campos originales de la fila); por bloque, `COPY _staging FROM STDIN` con `cursor.copy()` y `write_row()`; seleccionar ganadoras con `row_number() OVER (PARTITION BY id ORDER BY line_no DESC) = 1`; `WITH m AS (MERGE INTO health_records ... WHEN MATCHED AND (cols) IS DISTINCT FROM (cols) THEN UPDATE SET ..., loaded_at = now() WHEN NOT MATCHED THEN INSERT ... RETURNING merge_action() AS action) SELECT action, count(*) FROM m GROUP BY 1` (MERGE como CTE, no como subconsulta)
- [X] T016 [US1] Definir en scripts/load_data.py `LoadSummary` (dataclass) con `read`, `blank`, `valid`, `fixed`, `with_missing`, `inserted`, `updated`, `unchanged`, `rows_in_table` e imprimirlo con el formato de contracts/cli.md (la parte de rechazos llega en US2)
- [X] T017 [US1] Crear tests/data/test_valid.csv (cabecera exacta, BOM y CRLF, IDs ≥ 9.000.000) con una fila normal, una entre comillas, una con `' Germany  '`/`'MALE'`/`'Y'`, una con Age vacío y una con BMI `'n/a'`; y crear tests/test_load_data.py con un test que carga ese archivo vía `load_csv(db_conn, ...)` y comprueba registros normalizados (`Germany`, `Male`, `True`), `None` en los opcionales vacíos, `fixed` y `with_missing` del resumen, y que una copia LF sin BOM da el mismo resultado
- [X] T018 [US1] Ejecutar `make db-reset && make load` y comprobar 9.984 registros, 9 con algún opcional vacío y 20 países canónicos (consultas de quickstart.md §2)

**Checkpoint**: US1 funciona sola: carga correcta aunque los rechazos aún no se registren.

---

## Phase 4: User Story 2 - Explicar cada fila no cargada (Priority: P1)

**Goal**: Cada fila rechazada queda en `load_rejects` con línea, motivo, columna, valor y fila
original; el resumen concilia.

**Independent Test**: Tras `make load`, `load_rejects` tiene 16 filas con `reason` de
validación más las 12 de duplicados (US3); línea 816 → `Age` / `thirty` / `not_numeric`; línea
2659 → `column_count`.

### Tests for User Story 2 (escribir primero; deben fallar) ⚠️

- [X] T019 [US2] En tests/test_validation.py añadir casos de `Rejection`: fila con 13 y con 17 campos → `column_count` con `raw_fields` completos; fila con dos columnas inválidas → un solo `Rejection` cuyo `reason`/`column_name`/`raw_value` son los de la primera columna en orden de cabecera y cuyo `detail` nombra ambas; `raw_value` conserva el valor original sin recortar; fila corregible y además inválida → solo rechazo
- [X] T020 [US2] En tests/test_validation.py añadir la prueba de caracterización sin base de datos: recorrer data/coffee_health_raw.csv con `csv.reader` + `parse_row` y comprobar 10.012 filas leídas, 3 en blanco, 16 rechazos con el desglose `out_of_range` 6, `not_numeric` 5, `column_count` 3, `invalid_boolean` 2, 9.996 válidas, 56 con `fixed` y 9 con `missing`
- [X] T021 [P] [US2] Crear tests/data/test_rejects.csv (IDs ≥ 9.000.000) con una línea en blanco, una fila entre comillas, una fila de 13 campos, una con Age `'thirty'`, una con Smoking `'2'` y una con Country vacío; y en tests/test_load_data.py añadir un test que verifica en `load_rejects` (`source_file='test_rejects.csv'`) línea exacta, `reason`, `column_name`, `raw_value` y `raw_fields`, también después de la línea en blanco y de la fila entre comillas

### Implementation for User Story 2

- [X] T022 [US2] Completar en app/validation.py `Rejection` (dataclass frozen: `line_number`, `reason: RejectReason`, `column_name: str | None`, `raw_value: str | None`, `detail: str`, `raw_fields: tuple[str, ...]`) y la conversión de `ValidationError.errors()` a `Rejection` (primera columna en orden de cabecera; `detail` con todas, en inglés); `parse_row` devuelve `column_count` si `len(fields) != 16`. Hacer pasar T019–T020
- [X] T023 [US2] En scripts/load_data.py: al inicio de la transacción `DELETE FROM load_rejects WHERE source_file = %s` con `Path(csv_path).name`; por bloque, tras el `COPY` de válidas, `COPY load_rejects (source_file, line_number, reason, column_name, raw_value, detail, raw_fields) FROM STDIN` con los rechazos del bloque
- [X] T024 [US2] En scripts/load_data.py ampliar `LoadSummary` con `rejected`, `rejected_by_reason` y `rejects_in_table`; imprimir el desglose de motivos con conteo > 0 de mayor a menor (contracts/cli.md)
- [X] T025 [US2] En scripts/load_data.py verificar antes del commit los invariantes de data-model.md §6 contando en la base: `count(*) FROM load_rejects WHERE source_file = %s` = rechazadas + duplicadas, y `rows_in_table = rows_before + inserted` (`rows_before` se cuenta al empezar `load_csv`); lanzar `ReconciliationError` si no cuadran; `main()` captura errores fatales, imprime `error: ...` en stderr, hace `rollback()` y sale con código 1 (archivo, cabecera, conexión, invariantes); código 0 en éxito aunque haya rechazos (FR-027)

**Checkpoint**: US1 + US2: la carga explica cada rechazo y falla de forma segura si algo no cuadra.

---

## Phase 5: User Story 3 - Resolver IDs repetidos de forma determinista (Priority: P2)

**Goal**: Las apariciones anteriores de un ID quedan en `load_rejects` como `duplicate_exact` o
`duplicate_conflict`, con la línea ganadora.

**Independent Test**: Tras `make load`, `load_rejects` tiene 8 `duplicate_exact` y 4
`duplicate_conflict`; ID 3338 queda con Age 26 y Heart_Rate 86 (línea 3516) y la línea 3339
aparece como `duplicate_conflict`, `superseded by line 3516`.

### Tests for User Story 3 (escribir primero; deben fallar) ⚠️

- [X] T026 [P] [US3] Crear tests/data/test_duplicates.csv (IDs ≥ 9.000.000) con: un ID repetido idéntico, otro repetido con Age distinta, otro que aparece 3 veces, otro cuya última aparición es inválida y una anterior válida, y otro que solo difiere en un opcional vacío; y en tests/test_load_data.py añadir tests que comprueban el registro ganador (última válida), `duplicate_exact` frente a `duplicate_conflict`, el `detail` con la línea ganadora, que la aparición inválida cuenta como rechazo y no como duplicado, que el `raw_fields` de cada duplicado es la fila original sin normalizar (por ejemplo, conserva `' Germany  '`), y que `chunk_size=3` (duplicados en bloques distintos) da el mismo resultado que un solo bloque

### Implementation for User Story 3

- [X] T027 [US3] En scripts/load_data.py, antes del MERGE, insertar las perdedoras en `load_rejects` con un `INSERT ... SELECT` sobre `_staging`: `row_number() OVER (PARTITION BY id ORDER BY line_no DESC) > 1`, unidas a su ganadora; `reason = duplicate_exact` si las 15 columnas de datos `IS NOT DISTINCT FROM` las de la ganadora (seguro con NULL), si no `duplicate_conflict`; `detail = 'superseded by line ' || ganadora.line_no`; `raw_fields` = el `raw_fields` de la perdedora en `_staging`, es decir, la fila original (FR-020). Ampliar `LoadSummary` con `duplicates`, `duplicate_exact` y `duplicate_conflict` e incluirlos en los invariantes

**Checkpoint**: `make db-reset && make load` da exactamente los valores de referencia del inicio de este archivo.

---

## Phase 6: User Story 4 - Repetir la carga sin efectos secundarios (Priority: P2)

**Goal**: Recargar no cambia nada; un archivo actualizado actualiza por ID; un fallo no deja
nada a medias; una carga a la vez.

**Independent Test**: Segunda `make load` → `inserted 0`, `updated 0`, `unchanged 9984`,
`rejects in table 28`.

### Tests for User Story 4 (escribir primero; deben fallar) ⚠️

- [X] T028 [US4] En tests/test_load_data.py añadir tests de idempotencia y recarga con tests/data/test_valid.csv: dos cargas seguidas → la segunda con 0 insertadas, 0 actualizadas y mismos `count(*)` en ambas tablas; copia con un Heart_Rate cambiado (crear tests/data/test_valid_changed.csv) → 1 actualizada, 0 insertadas; recarga donde la única fila de un ID ya cargado es inválida → registro existente intacto y fila en `load_rejects` (Q3); cargar test_rejects.csv no borra los rechazos de test_duplicates.csv
- [X] T029 [US4] En tests/test_load_data.py añadir tests de atomicidad: cabecera inválida → ninguna tabla cambia; forzar un fallo después del MERGE (monkeypatch de la verificación de invariantes para que lance) ejecutando la carga dentro de `with db_conn.transaction():` (savepoint, para no perder el estado previo del propio test) y capturando la excepción → `health_records` y `load_rejects` con los mismos conteos que antes; un `INSERT` directo con `age = 150`, `gender = 'x'` o `country = NULL` falla por `CHECK`/`NOT NULL` (FR-024); `main([ruta_inexistente])` devuelve 1, escribe `error:` en stderr y no cambia nada; `main([...])` con `settings.database_url` apuntando a un puerto cerrado (`postgresql://postgres:postgres@127.0.0.1:1/coffee`, vía monkeypatch) devuelve 1 (FR-027)

### Implementation for User Story 4

- [X] T030 [US4] En scripts/load_data.py ejecutar `SELECT pg_advisory_xact_lock(<constante int>)` como primera sentencia de `load_csv` (research R10) y confirmar que todo el flujo (DELETE, COPY, INSERT de duplicados, MERGE, verificación) ocurre en una única transacción sin commits intermedios; `load_csv` nunca hace commit. Hacer pasar T028–T029

**Checkpoint**: quickstart.md §3 (segunda carga) da `inserted 0`, `updated 0`, `unchanged 9984`.

---

## Phase 7: User Story 5 - Procesar archivos mucho más grandes (Priority: P3)

**Goal**: Memoria constante y al menos 20.000 filas/s con 1 millón de filas.

**Independent Test**: Cargar un CSV sintético de ~1 millón de filas; conteos conciliados y
memoria pico comparable a la del archivo actual.

- [X] T031 [P] [US5] Crear scripts/make_big_csv.py: `make_big_csv.py <origen> <copias> <destino>` replica las filas de datos del CSV real `copias` veces desplazando el ID (`id + k * 10_000_000`, solo si el ID es entero; si no, la fila se copia tal cual), conservando la suciedad del original; `copias` entre 1 y 214 (con más, los IDs superan 2.147.483.647 y la medición contaría rechazos), con un error claro fuera de ese rango; escribe en streaming, sin cargar el archivo en memoria; con type hints y sin dependencias nuevas
- [X] T032 [US5] En scripts/load_data.py añadir al resumen la línea `Elapsed <s> s (<rows/s> rows/s)` y, con `--profile-memory`, el pico de `tracemalloc`; medir con 1 millón de filas (quickstart.md §5) y con chunk 10.000 frente a 50.000; anotar tiempo, filas/s y memoria pico en specs/001-csv-loader/research.md R15; borrar el CSV grande y restaurar la base (`make db-reset && make load`)

**Checkpoint**: SC-005 y SC-006 medidos y documentados.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T033 Ejecutar `make lint` (ruff check, ruff format --check, mypy strict) y corregir; la única excepción permitida es `# noqa: E402` en scripts/load_data.py (research R11)
- [X] T034 [P] Revisar docstrings y nombres en inglés en app/validation.py y scripts/load_data.py (constitución, convenciones); sin comentarios que repitan el código
- [X] T035 Ejecutar quickstart.md completo (§1–§4) desde base limpia y comprobar cada valor esperado; anotar el `Elapsed` de `make load` y comprobar que es menor de 10 s (SC-005); `docker compose stop db` + `uv run pytest tests/test_validation.py` pasa sin base de datos
- [X] T036 [P] Crear NOTES.md con un borrador de las secciones del loader (schema, cleaning and validation, duplicates, lint exceptions, scale del loader) a partir de spec.md, research.md y los resultados reales de T018, T032 y T035; las secciones de API se completan en la feature 002

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)** → **Foundational (2)** → historias → **Polish (8)**
- US1 (fase 3) es la base de carga; US2–US5 la amplían en los mismos archivos

### User Story Dependencies

- **US1 (P1)**: tras la fase 2
- **US2 (P1)**: tras US1 (amplía `parse_row`, `load_csv` y `LoadSummary`)
- **US3 (P2)**: tras US2 (inserta en `load_rejects`)
- **US4 (P2)**: tras US3 (los invariantes completos dependen de los duplicados)
- **US5 (P3)**: T031 puede empezar tras la fase 2; T032 tras US4

### Within Each User Story

- Tests primero y en rojo; luego implementación hasta verde
- app/validation.py antes que scripts/load_data.py
- Un escritor por archivo: las tareas sobre tests/test_validation.py, app/validation.py o scripts/load_data.py son secuenciales entre sí

### Parallel Opportunities

- T002 junto con T001
- T005 y T006 en paralelo (archivos distintos), después de T003–T004
- T021 (CSV + test de integración) en paralelo con T019–T020 (tests puros)
- T026 en paralelo con cualquier tarea que no toque tests/test_load_data.py
- T031 en paralelo con US2–US4
- T034 y T036 en paralelo al final

---

## Parallel Example: User Story 2

```text
Task: "T019–T020: casos de Rejection y caracterización en tests/test_validation.py"
Task: "T021: tests/data/test_rejects.csv y su test en tests/test_load_data.py"
```

## Parallel Example: Foundational

```text
Task: "T005: constantes y StrEnum en app/validation.py"
Task: "T006: fixture db_conn en tests/conftest.py"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Fases 1 y 2
2. Fase 3 (US1): `make load` deja 9.984 registros correctos
3. **Parar y validar** con T018

### Incremental Delivery

1. US1 → carga correcta
2. US2 → rechazos explicables y conciliación (mínimo entregable para la prueba)
3. US3 → duplicados explicados; los conteos coinciden con los valores de referencia
4. US4 → idempotencia y atomicidad probadas
5. US5 → escala medida
6. Polish → lint, quickstart y borrador de NOTES.md

### Commits sugeridos

Un commit por fase cerrada (`feat(001): ...`, `test(001): ...`), siempre con `make test` y
`make lint` en verde (constitución VI).

---

## Notes

- [P] = archivos distintos, sin dependencias pendientes
- Cada valor esperado de este archivo sale de data-model.md §6; si un conteo no coincide, se
  investiga antes de cambiar la expectativa
- Las pruebas de integración usan IDs ≥ 9.000.000 y archivos `test_*.csv`, y se deshacen con el
  fixture `db_conn`

## Desviaciones al implementar

Lo que se hizo distinto de lo escrito arriba, y por qué:

- **T002, T017, T021, T026, T028** — los CSV de prueba no son archivos en `tests/data/` (no se
  creó): cada test escribe el suyo byte a byte en `tmp_path`. Git reescribe los finales de línea
  (LF/CRLF) según la configuración de cada clon, y los tests de BOM y CRLF dependerían de eso.
- **T010** — los patrones usan `[0-9]`, no `\d`: en un patrón `str`, `\d` acepta dígitos no ASCII
  como `４０`, que T007 exige rechazar.
- **T012** — `ParsedRow` es un `NamedTuple`, no un dataclass frozen: se crea uno por fila válida.
- **T003** — el `CHECK` de `id` es `id >= 1`; el tope superior ya lo da el tipo `integer`.
- **T015, T027** — `_staging` toma sus tipos de `health_records` (`CREATE TABLE AS ... WITH NO
  DATA`) y guarda la fila original como un texto JSON (`raw_row`) en vez de `text[]`: es más
  barato de enviar por `COPY` y solo se desempaqueta para los duplicados. Las perdedoras se
  borran de `_staging` al registrarlas, así el `MERGE` lee una fila por ID sin `row_number()`.
- **T013, T032** — el bloque por defecto es 10.000 filas, no 50.000 (medición en research R15).
