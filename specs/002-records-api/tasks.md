---

description: "Task list for feature 002-records-api"
---

# Tasks: API de consulta de registros Coffee Health

**Input**: Design documents from `specs/002-records-api/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: Incluidas. La constitución (principio II) exige TDD para la lógica pura (validación de
parámetros y construcción de consultas) y pruebas de integración contra la base real para las
rutas. Las tareas de test van antes que su implementación y deben fallar primero.

**Organization**: Tareas agrupadas por historia de usuario (US1–US5 de spec.md), más una fase
para el error temporal (FR-030), que no pertenece a ninguna historia.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Puede ejecutarse en paralelo (archivo distinto, sin dependencias pendientes)
- **[Story]**: Historia a la que pertenece (US1…US5)
- Rutas relativas a la raíz del repositorio

## Valores de referencia

Con `data/coffee_health_raw.csv` cargado: 9.984 registros, IDs de 1 a 10.000 · Germany 497 ·
Germany o Spain 983 · Germany y Female 249 · fumadores 2.001 · no fumadores 7.983 · edad 30–40
3.540 · sin edad 2 · resumen por género: Female 4.989, Male 4.767, Other 225, sin dato 3 ·
tazas de café: 9 grupos (0–8) · filas no cargadas 28 (8, 6, 5, 4, 3, 2).

Los tests de integración no usan estos valores: insertan sus propias filas (research R14). Los
valores sirven para el quickstart.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirmar la línea base antes de tocar nada.

- [X] T001 Verificar la línea base en la rama `002-records-api`: `make db-up`, `make test` (210 passed) y `make lint` en verde, sin cambios de código; si algo falla, anotar el error exacto en specs/002-records-api/research.md antes de seguir

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Índice, configuración del pool, modelo de registro y datos de prueba que usan todas
las historias.

**⚠️ CRITICAL**: Ninguna historia empieza antes de terminar esta fase.

- [X] T002 Crear db/init/003_indexes.sql con `create index health_records_country_id_idx on public.health_records (country, id);` y un comentario SQL breve con su porqué (research R7: un país con pocas filas o inexistente recorre toda la tabla sin él; para un país frecuente basta la PK) y su costo (+39 % en la inserción). No tocar 001_notes.sql ni 002_health_records.sql. Aplicar con `make db-reset && make load` y comprobar el índice con `\d health_records` en `make db-shell`
- [X] T003 [P] En app/config.py añadir a `Settings` los cuatro ajustes de data-model.md §4 con estos nombres y valores por defecto exactos: `db_pool_min_size: int = 1`, `db_pool_max_size: int = 10`, `db_pool_timeout_seconds: float = 5.0`, `db_statement_timeout_ms: int = 5000`; `database_url` no cambia. En .env.example añadir las cuatro variables (`DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE`, `DB_POOL_TIMEOUT_SECONDS`, `DB_STATEMENT_TIMEOUT_MS`) comentadas con su valor por defecto y una línea que explique cada una
- [X] T004 En app/db.py hacer que `create_pool()` lea de `settings`: `min_size=settings.db_pool_min_size`, `max_size=settings.db_pool_max_size`, `timeout=settings.db_pool_timeout_seconds`; y que cada conexión se abra con `kwargs={"row_factory": dict_row, "options": f"-c statement_timeout={settings.db_statement_timeout_ms}", "prepare_threshold": None}` (research R8, R11). El límite va en las conexiones del pool, nunca en `DATABASE_URL`: scripts/load_data.py usa su propia conexión y no debe heredarlo. `make test` sigue en verde
- [X] T005 [P] Crear app/schemas.py con `Record(BaseModel)` exactamente como data-model.md §2: `id: int`; `age: int | None`; `gender: Gender | None`; `country: str`; `coffee_intake: float`; `caffeine_mg: float`; `sleep_hours: float | None`; `sleep_quality: SleepQuality | None`; `bmi: float | None`; `heart_rate: int | None`; `stress_level: StressLevel | None`; `physical_activity_hours: float | None`; `health_issues: HealthIssues | None`; `occupation: Occupation | None`; `smoking: bool | None`; `alcohol_consumption: bool | None`. Los `StrEnum` se importan de app/validation.py; las columnas `numeric` son `float` (research R10); `loaded_at` no se expone. Y `RecordPage(BaseModel)`: `items: list[Record]`, `next_after_id: int | None`, `limit: int`
- [X] T006 [P] En tests/conftest.py añadir, conservando `client` y `db_conn`: (a) la función `record_row(record_id: int, **overrides: object) -> dict[str, object]` que devuelve una fila completa y válida de `health_records` (por defecto `country="Testland"`, `coffee_intake=3.5`, `caffeine_mg=300`, y el resto de columnas con un valor dentro de sus `CHECK`); (b) el fixture `seed` (scope function) que abre una conexión propia con `autocommit=True`, cede un objeto con dos métodos, `records(rows)` (inserta en `health_records`) y `rejects(rows)` (inserta en `load_rejects`, con `raw_fields` obligatorio), y al terminar borra exactamente las claves que insertó (los `id` de `health_records` y los pares `(source_file, line_number)` de `load_rejects` que recibió), nunca por rango ni por patrón, para no tocar datos que no son suyos. Los datos deben quedar confirmados: las rutas leen por el pool y no ven transacciones abiertas (research R14)

**Checkpoint**: `make db-reset && make load` crea el índice; `make test` y `make lint` en verde.

---

## Phase 3: User Story 1 - Listar registros por páginas (Priority: P1) 🎯 MVP

**Goal**: `GET /records` devuelve una página acotada en orden de ID y el cursor de la siguiente.

**Independent Test**: Recorrer `GET /records?limit=200` siguiendo `next_after_id` hasta `null` →
50 páginas y 9.984 registros distintos en orden ascendente.

### Tests for User Story 1 (escribir primero; deben fallar) ⚠️

- [X] T007 [US1] Crear tests/test_filters.py (sin base de datos, `pytest.mark.parametrize`) con los casos de paginación de `RecordListParams`, validando con `RecordListParams.model_validate({...})` a partir de cadenas, como llegan de la URL: sin parámetros → `limit == 50` y `after_id == 0`; `limit` `"1"` y `"200"` válidos; `"0"`, `"201"`, `"abc"`, `"1.5"` inválidos con el error situado en `limit`; `after_id` `"0"` y `"10000"` válidos; `"-1"` y `"abc"` inválidos con el error en `after_id`; un parámetro desconocido (`min_agee`) → error `extra_forbidden` situado en `min_agee`; y `build_where(RecordListParams())` devuelve SQL vacío y lista de parámetros vacía. Añadir un helper `errors(model, data) -> dict[str, str]` que devuelve `{parámetro: tipo de error}` de la `ValidationError`, para reutilizarlo en las demás historias
- [X] T008 [US1] Crear tests/test_records_api.py (con base de datos, fixtures `client` y `seed`) con los casos de listado sobre 5 filas sembradas (IDs 9.000.001–9.000.005), usando `after_id=8999999` para ver solo esas filas: `limit=2` → 2 elementos en orden ascendente y `next_after_id == 9000002`; seguir el cursor hasta `null` devuelve los 5 IDs exactamente una vez; `limit=5` (última página exactamente llena) → `next_after_id` es `null`; `after_id=9000005` → `items == []` y `next_after_id` `null`; `GET /records` sin parámetros → `limit == 50`, como mucho 50 elementos y IDs ascendentes; una fila sembrada con `age=None` y `bmi=None` llega con esos campos en `null` y el resto con valor; `coffee_intake`, `caffeine_mg` y `bmi` son números JSON (`isinstance(valor, float)`), no cadenas; `smoking` y `alcohol_consumption` son booleanos JSON (`True`/`False`), no `0`/`1` ni cadenas (FR-020); cada elemento tiene exactamente las 16 claves de `Record` (sin `loaded_at`); `limit=201` → 422

### Implementation for User Story 1

- [X] T009 [US1] Crear app/filters.py con: `RecordFilters(BaseModel)` con `model_config = ConfigDict(extra="forbid")` y, por ahora, sin campos; `RecordListParams(RecordFilters)` con `limit: int = Field(50, ge=1, le=200)` y `after_id: int = Field(0, ge=0)`; y la función pura `build_where(filters: RecordFilters) -> tuple[sql.Composable, list[object]]`, que por ahora devuelve `sql.SQL("")` y `[]` (research R3, R6). Sin imports de psycopg distintos de `psycopg.sql`, sin conexión. Hacer pasar T007
- [X] T010 [US1] Crear app/records.py con `router = APIRouter()` y `GET /records` (función `def`, parámetros `Annotated[RecordListParams, Query()]`, respuesta `RecordPage`): ejecuta `SELECT <las 16 columnas de Record> FROM health_records WHERE id > %s [AND <where>] ORDER BY id LIMIT %s` pidiendo `limit + 1` filas; si llegan `limit + 1`, descarta la última y `next_after_id` es el `id` del último elemento devuelto; si no, `next_after_id = None` (research R1, R2). Las columnas se componen con `psycopg.sql.Identifier`; sin `OFFSET` y sin `count(*)`. La conexión llega con la dependencia `get_conn` de app/db.py
- [X] T011 [US1] En app/main.py incluir el router (`app.include_router(router)`) y quitar el comentario `# TODO (Part 2)`; conservar `/health` y `/notes` sin cambios. Hacer pasar T008 y comprobar en `make dev` que `/docs` muestra `GET /records` con `limit` (mínimo 1, máximo 200, por defecto 50) y `after_id`

**Checkpoint**: US1 funciona sola: el listado se recorre completo por páginas.

---

## Phase 4: User Story 2 - Filtrar registros (Priority: P1)

**Goal**: El listado acepta los filtros de selección múltiple, el booleano y los rangos, y la
paginación recorre solo lo que cumple los filtros.

**Independent Test**: `GET /records?country=Germany&limit=200` siguiendo el cursor → 497
registros, todos de Germany.

### Tests for User Story 2 (escribir primero; deben fallar) ⚠️

- [X] T012 [US2] En tests/test_filters.py añadir los casos de normalización y de construcción del `WHERE`: `country` `["germany"]`, `[" SPAIN "]`, `["usa"]`, `["South KOREA"]` → `Germany`, `Spain`, `USA`, `South Korea`; `["Atlantis "]` → `Atlantis` (desconocido: recortado y conservado, sin error); `[""]` y `["   "]` → `[""]` (vacío: sin error, no coincide con nada); un valor repetido (`["Germany", "germany"]`) se acepta; `gender` `["female", " MALE "]` → `[Gender.FEMALE, Gender.MALE]`; `sleep_quality` `["GOOD"]` y `stress_level` `["low"]` → sus miembros; `smoking` `"true"`/`"false"` → `True`/`False`; `min_age` entero; `min_coffee_intake` y `min_bmi` de tipo `Decimal`; un solo valor de un filtro de selección llega como lista de un elemento. Para `build_where`: cada filtro por separado produce su condición y sus parámetros (`"country" = any(%s)` con `[["Germany", "Spain"]]`; `"gender" = any(%s)`; `"sleep_quality" = any(%s)`; `"stress_level" = any(%s)`; `"smoking" = %s`; `"age" >= %s`; `"age" <= %s`; `"coffee_intake" >= %s`; `"coffee_intake" <= %s`; `"bmi" >= %s`; `"bmi" <= %s`), comparando el texto con `as_string()` sin conexión; varios filtros se unen con ` and ` en el orden de declaración; ningún valor del cliente aparece en el texto SQL (probar con `country=["x'; drop table health_records; --"]`)
- [X] T013 [US2] En tests/test_records_api.py añadir los casos de filtros sobre filas sembradas con dos países (`Testland` y `Testonia`) y valores variados, siempre con `after_id=8999999`: `country=Testland&country=Testonia` devuelve las de ambos; `country=Testland&gender=Female` solo las que cumplen las dos; `gender=female` (minúsculas) equivale a `gender=Female`; `smoking=true` y `smoking=false`; `min_age=30&max_age=40` incluye los extremos 30 y 40; solo `min_age` o solo `max_age` deja el otro extremo abierto; una fila con `age=None` no aparece con ningún filtro de edad y sí sin él; `min_bmi` y `max_coffee_intake` con decimales; `country=Atlantis` → 200 con `items == []` y `next_after_id` `null`; `country=` (vacío) y `min_age=500` → 200 con `items == []`, sin error; un filtro cuyo resultado ocupa varias páginas (`limit=2`) se recorre con el cursor devolviendo cada fila que lo cumple exactamente una vez; y una fila insertada con `seed` entre dos páginas, con ID mayor que el cursor, aparece en una página posterior sin que ninguna se repita

### Implementation for User Story 2

- [X] T014 [US2] En app/filters.py añadir a `RecordFilters` los campos de data-model.md §3, todos opcionales con valor por defecto `None` y con cada `min_*` declarado antes que su `max_*`: `country: list[str] | None`; `gender: list[Gender] | None`; `sleep_quality: list[SleepQuality] | None`; `stress_level: list[StressLevel] | None`; `smoking: bool | None`; `min_age: int | None`, `max_age: int | None`; `min_coffee_intake: Decimal | None`, `max_coffee_intake: Decimal | None`; `min_bmi: Decimal | None`, `max_bmi: Decimal | None`. Los cuatro filtros de selección llevan un `BeforeValidator` que recorta y compara con `casefold()` (research R5): las categorías se traducen al miembro de su `StrEnum` (lo que no esté en el catálogo se deja pasar para que falle con el error `enum` estándar); `country` se traduce con el catálogo `COUNTRIES` de app/validation.py y, si no está, se deja recortado tal cual. Una sola función de normalización para los tres `StrEnum`
- [X] T015 [US2] En app/filters.py completar `build_where` con la tabla de data-model.md §3: selección múltiple → `columna = ANY(%s)` con la lista como un solo parámetro; `smoking` → `smoking = %s`; `min_x`/`max_x` → `x >= %s`/`x <= %s`; solo los filtros presentes, unidos con ` AND ` en el orden de declaración. Nombres de columna con `sql.Identifier` desde constantes del código; ningún valor del cliente en el texto SQL (research R6). app/records.py ya antepone `id > %s` y usa el resultado. Hacer pasar T012–T013

**Checkpoint**: US1 + US2: listado filtrable; quickstart.md §3 da 497, 983 y 249.

---

## Phase 5: User Story 3 - Recibir errores claros ante parámetros inválidos (Priority: P2)

**Goal**: Todo parámetro inválido o desconocido devuelve 422 nombrando el parámetro, y todos
los errores de una petición llegan juntos.

**Independent Test**: `GET /records?min_age=50&max_age=20&limit=0&min_agee=1` → 422 con tres
errores situados en `max_age`, `limit` y `min_agee`.

### Tests for User Story 3 (escribir primero; deben fallar) ⚠️

- [X] T016 [US3] En tests/test_filters.py añadir los casos de error de contracts/api.md: `min_age=50, max_age=20` → error de tipo `range_inverted` situado en `max_age` cuyo mensaje nombra `min_age`; lo mismo para `coffee_intake` y `bmi`; mínimo igual a máximo es válido; `min_age="abc", max_age="20"` → un solo error, en `min_age`; solo `max_age` sin `min_age` es válido; `gender=["Robot"]` → error en `gender` cuyo mensaje lista `Male`, `Female`, `Other`; `sleep_quality=["Great"]` y `stress_level=["Extreme"]` → error en su parámetro; `min_bmi="abc"`, `min_age="30.5"`, `smoking="maybe"` → error en su parámetro; `min_bmi="NaN"` y `max_bmi="Infinity"` → error (Pydantic ya los rechaza con `finite_number`; el test lo deja fijado); y varios inválidos a la vez (`limit="0"`, `gender=["Robot"]`, `min_age="50"`, `max_age="20"`, `min_agee="1"`) → cuatro errores en la misma `ValidationError`, uno por parámetro (`limit`, `gender`, `max_age`, `min_agee`), incluido el del rango (FR-018, research R4)
- [X] T017 [US3] En tests/test_records_api.py añadir los casos por HTTP: cada petición de la tabla de 422 de contracts/api.md que afecta a `/records` devuelve 422, el cuerpo es `{"detail": [...]}` y algún elemento tiene `loc == ["query", "<parámetro>"]`; `GET /records?min_age=50&max_age=20&limit=0&min_agee=1` devuelve los tres parámetros en una sola respuesta; ninguna respuesta 422 contiene la clave `items`

### Implementation for User Story 3

- [X] T018 [US3] En app/filters.py añadir a `RecordFilters` un `field_validator` sobre `max_age`, `max_coffee_intake` y `max_bmi` que lee el `min_*` correspondiente de `info.data` y, si ambos tienen valor y el máximo es menor, lanza `PydanticCustomError("range_inverted", "{max_name} must be greater than or equal to {min_name}", {...})` (research R4: no un `model_validator`, que no se ejecuta si otro parámetro ya falló). Si el mínimo no validó, no hay nada que comparar. Hacer pasar T016–T017

**Checkpoint**: US1–US3: cada caso de la tabla de errores de contracts/api.md para `/records` devuelve 422.

---

## Phase 6: User Story 4 - Ver un resumen por grupo (Priority: P3, opcional)

**Goal**: `GET /records/summary` devuelve, con los mismos filtros, el conteo y tres promedios por
grupo, y los conteos suman el total.

**Independent Test**: `GET /records/summary?group_by=gender` → 4.989, 4.767, 225 y 3 (clave
`null`), que suman 9.984.

### Tests for User Story 4 (escribir primero; deben fallar) ⚠️

- [X] T019 [US4] En tests/test_filters.py añadir los casos de `SummaryParams`: `group_by` es obligatorio (sin él, error situado en `group_by`); admite exactamente `country`, `gender`, `sleep_quality`, `stress_level` y `coffee_cups`; `group_by="planet"` → error en `group_by`; acepta los mismos filtros que el listado (`country`, `min_age`…) y `build_where` produce para ellos lo mismo que con `RecordListParams`; `limit` y `after_id` no existen aquí (`extra_forbidden`); y `GROUP_BY` tiene exactamente esas cinco claves
- [X] T020 [US4] En tests/test_records_api.py añadir los casos de resumen sobre filas sembradas en `Testland` y `Testonia`. El resumen no tiene cursor, así que cada caso se aísla de los datos cargados filtrando por esos países (`country=Testland` salvo que se indique otra cosa): `group_by=gender` devuelve un grupo por valor más un grupo con `key` `null` para las filas con `gender=None`, ordenados por clave con el `null` al final; la suma de los `count` es igual al número de filas que devuelve `/records` con los mismos filtros (SC-006), también al añadir `smoking=true`; los promedios se calculan a mano en el test y coinciden redondeados a dos decimales; una fila con `sleep_hours=None` no cuenta en `avg_sleep_hours` de su grupo; un grupo donde todas las filas tienen `bmi=None` trae `avg_bmi` `null`, no `0`; `group_by=coffee_cups` con `coffee_intake` 2.0, 2.9 y 3.0 → claves enteras `2` (dos filas) y `3` (una); `group_by=country` con `country=Testland&country=Testonia` → un grupo por país; `country=Atlantis` → `[]`; los promedios y conteos son números JSON; `group_by=planet` y la ausencia de `group_by` → 422 con `loc == ["query", "group_by"]`

### Implementation for User Story 4

- [X] T021 [P] [US4] En app/schemas.py añadir `SummaryRow(BaseModel)`: `key: str | int | None`, `count: int`, `avg_sleep_hours: float | None`, `avg_coffee_intake: float | None`, `avg_bmi: float | None` (data-model.md §2)
- [X] T022 [US4] En app/filters.py añadir el `StrEnum` `GroupBy` (`country`, `gender`, `sleep_quality`, `stress_level`, `coffee_cups`), `SummaryParams(RecordFilters)` con `group_by: GroupBy` obligatorio, y el diccionario fijo `GROUP_BY: dict[GroupBy, sql.Composable]`: las cuatro primeras claves a `sql.Identifier(<columna>)` y `coffee_cups` a `sql.SQL("floor(coffee_intake)::int")` (research R12). Hacer pasar T019
- [X] T023 [US4] En app/records.py añadir `GET /records/summary` (función `def`, parámetros `Annotated[SummaryParams, Query()]`, respuesta `list[SummaryRow]`): `SELECT <GROUP_BY[group_by]> AS key, count(*) AS count, round(avg(sleep_hours), 2) AS avg_sleep_hours, round(avg(coffee_intake), 2) AS avg_coffee_intake, round(avg(bmi), 2) AS avg_bmi FROM health_records [WHERE <build_where>] GROUP BY 1 ORDER BY 1 NULLS LAST`. Sin código para los nulos: `GROUP BY` ya forma el grupo `NULL` y `avg` ya los ignora. Hacer pasar T020

**Checkpoint**: el resumen concilia con el listado para cualquier combinación de filtros.

---

## Phase 7: User Story 5 - Consultar las filas no cargadas (Priority: P3, opcional)

**Goal**: `GET /rejects` lista por páginas las filas no cargadas y `GET /rejects/summary` las
cuenta por motivo.

**Independent Test**: `GET /rejects` → 28 filas en orden de línea; `GET /rejects/summary` → 8,
6, 5, 4, 3 y 2.

### Tests for User Story 5 (escribir primero; deben fallar) ⚠️

- [X] T024 [US5] En tests/test_filters.py añadir los casos de `RejectListParams` y `RejectSummaryParams`: `limit` con las mismas reglas que el listado (por defecto 50, 1–200); `after_source_file` y `after_line_number` juntos son válidos y ausentes también; el cursor incompleto (solo `after_line_number`, o solo `after_source_file`) → un error de tipo `cursor_incomplete` situado siempre en `after_line_number`, cuyo mensaje nombra los dos parámetros, y que llega junto a otros errores de la misma petición (`limit="0"`); `after_line_number="-1"` → error; `reason` `["not_numeric", " OUT_OF_RANGE "]` → `[RejectReason.NOT_NUMERIC, RejectReason.OUT_OF_RANGE]`; `reason=["typo"]` → error en `reason`; `source_file` se conserva tal cual; parámetros desconocidos → `extra_forbidden` en los dos modelos. Y los casos de la función pura `build_reject_where`, comparando el texto con `as_string()` sin conexión: sin argumentos → SQL vacío y sin parámetros; `after=("test_api_a.csv", 4)` → `("source_file", "line_number") > (%s, %s)` con `["test_api_a.csv", 4]`; `reasons` → `"reason" = any(%s)` con la lista como un solo parámetro; `source_file` → `"source_file" = %s`; los tres juntos, unidos con ` and ` en ese orden; ningún valor del cliente aparece en el texto SQL
- [X] T025 [US5] En tests/test_records_api.py añadir los casos de filas no cargadas, sembrando con `seed.rejects` filas de dos archivos (`test_api_a.csv` con líneas 5, 9 y 12, y `test_api_b.csv` con líneas 3 y 7) de motivos variados, uno de ellos `duplicate_conflict` con `column_name=None`, `raw_value=None` y `detail="superseded by line 12"`: `source_file=test_api_a.csv` devuelve sus 3 filas en orden de línea; cada elemento tiene exactamente las claves `source_file`, `line_number`, `reason`, `column_name`, `raw_value`, `detail` (sin `raw_fields` ni `loaded_at`); la fila duplicada trae `column_name` y `raw_value` en `null` y su `detail`; con `limit=2` y `after_source_file=test_api_a.csv&after_line_number=4`, seguir `next_after` recorre las 5 filas sembradas, las de `a` antes que las de `b`, exactamente una vez, y la página que agota el recorrido trae `next_after` `null`; con `limit=5` desde ese mismo cursor (última página exactamente llena) `next_after` también es `null`; `source_file=test_api_a.csv&reason=not_numeric&reason=out_of_range` filtra por cualquiera de los dos motivos dentro de ese archivo; `source_file=no_existe.csv` → `items == []`; `GET /rejects/summary?source_file=test_api_a.csv` devuelve un elemento por motivo con su conteo, de mayor a menor y, a igualdad, por motivo; `reason=typo` y `after_line_number=5` sin `after_source_file` → 422

### Implementation for User Story 5

- [X] T026 [P] [US5] En app/schemas.py añadir, según data-model.md §2: `Reject(BaseModel)` con `source_file: str`, `line_number: int`, `reason: RejectReason`, `column_name: str | None`, `raw_value: str | None`, `detail: str`; `RejectCursor(BaseModel)` con `source_file: str` y `line_number: int`; `RejectPage(BaseModel)` con `items: list[Reject]`, `next_after: RejectCursor | None`, `limit: int`; y `ReasonCount(BaseModel)` con `reason: RejectReason` y `count: int`
- [X] T027 [US5] En app/filters.py añadir `RejectListParams(BaseModel)` con `extra="forbid"`: `limit: int = Field(50, ge=1, le=200)`; `after_source_file: str | None = None`; `after_line_number: int | None = Field(None, ge=0)`; `reason: list[RejectReason] | None = None` con el mismo normalizador que las categorías; `source_file: str | None = None`. El cursor se valida con un `field_validator` sobre `after_line_number`, declarado con `validate_default=True` y después de `after_source_file` para poder leerlo de `info.data`: si viene uno sin el otro lanza `PydanticCustomError("cursor_incomplete", "after_source_file and after_line_number must be given together")`, situado siempre en `after_line_number` (contracts/api.md). Añadir `RejectSummaryParams(BaseModel)` con `extra="forbid"` y `source_file: str | None = None`. Añadir la función pura `build_reject_where(*, after: tuple[str, int] | None = None, reasons: Sequence[RejectReason] | None = None, source_file: str | None = None) -> tuple[sql.Composable, list[object]]`, que devuelve solo las condiciones presentes unidas con ` AND ` (`(source_file, line_number) > (%s, %s)`, `reason = ANY(%s)`, `source_file = %s`) y SQL vacío sin argumentos; la usan las dos rutas (constitución II: la construcción de consultas es pura y se prueba sin base). Hacer pasar T024
- [X] T028 [US5] En app/records.py añadir `GET /rejects` (respuesta `RejectPage`): `SELECT source_file, line_number, reason, column_name, raw_value, detail FROM load_rejects [WHERE <build_reject_where(after, reasons, source_file)>] ORDER BY source_file, line_number LIMIT %s` pidiendo `limit + 1` filas, con `next_after` construido igual que en `/records` (research R13); y `GET /rejects/summary` (respuesta `list[ReasonCount]`): `SELECT reason, count(*) AS count FROM load_rejects [WHERE <build_reject_where(source_file=...)>] GROUP BY reason ORDER BY count(*) DESC, reason`. Las condiciones salen siempre de `build_reject_where`; el router no compone ninguna. Hacer pasar T025

**Checkpoint**: las cuatro rutas de contracts/api.md responden; quickstart.md §3 completo.

---

## Phase 8: Error temporal (FR-030, SC-008)

**Purpose**: Base saturada, caída o consulta demasiado lenta → 503 en tiempo acotado. No
pertenece a ninguna historia.

### Tests (escribir primero; deben fallar) ⚠️

- [X] T029 En tests/test_records_api.py añadir los casos de error temporal, cada uno creando su propio `TestClient(app)` después de cambiar `settings` con `monkeypatch` (el pool se crea al entrar en el `with`): (a) pool agotado: con `db_pool_min_size=1`, `db_pool_max_size=1` y `db_pool_timeout_seconds=0.3`, tomar la única conexión con `app.state.pool.getconn()`, pedir `GET /records` → 503 con cuerpo `{"detail": ...}` en menos de 2 segundos; devolver la conexión con `putconn` y comprobar que la siguiente petición da 200; (b) consulta demasiado lenta: con `db_statement_timeout_ms=200`, abrir otra conexión, ejecutar `LOCK TABLE health_records IN ACCESS EXCLUSIVE MODE` dentro de una transacción sin confirmar, pedir `GET /records` → 503; hacer `rollback` (en un `finally`) y comprobar que la siguiente petición da 200; (c) una conexión del pool tiene `current_setting('statement_timeout')` igual al valor configurado y `prepare_threshold is None`

### Implementation

- [X] T030 En app/main.py registrar un manejador de excepciones para `psycopg_pool.PoolTimeout`, `psycopg.errors.QueryCanceled` y `psycopg.OperationalError` que responde `JSONResponse` con estado 503 y `{"detail": "Database is busy or unavailable; retry shortly."}` (research R11). Hacer pasar T029

**Checkpoint**: ninguna petición se queda colgada: saturación y consultas lentas devuelven 503.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T031 Documentación interactiva (FR-033): en app/filters.py dar a cada parámetro una `description` en inglés (qué filtra, que es repetible cuando lo es, que no distingue mayúsculas); en app/records.py dar a cada ruta `summary` y un docstring breve en inglés. En tests/test_records_api.py añadir un test sobre `GET /openapi.json`: existen las cuatro rutas; en `/records`, `limit` declara mínimo 1, máximo 200 y valor por defecto 50; `gender` referencia un enum con `Male`, `Female`, `Other`; `group_by` de `/records/summary` es obligatorio y lista sus cinco valores; las cuatro rutas de la feature solo exponen el método `GET` (FR-032, solo lectura); y siguen existiendo `/health` y `/notes` (FR-034)
- [X] T032 Ejecutar `make lint` (ruff check, ruff format --check, mypy strict) y corregir; no se prevé ninguna excepción nueva (`noqa`, `type: ignore`); si hiciera falta alguna, lleva su motivo y se lista en NOTES.md
- [X] T033 [P] Revisar docstrings y nombres en inglés en app/schemas.py, app/filters.py, app/records.py y app/db.py (constitución, convenciones); sin comentarios que repitan el código
- [X] T034 Medir SC-003 (quickstart.md §4): generar el archivo de 1 millón de filas con scripts/make_big_csv.py, cargarlo anotando el `Elapsed` (para comparar con los 50 s medidos sin el índice, research R7) y medir de extremo a extremo con `TestClient` (mediana de 20 repeticiones) `GET /records?limit=50`, `GET /records?limit=50&after_id=990009900` y las dos mismas con `country=Germany`; comprobar que las cuatro tardan menos de 200 ms y que la profunda tarda como máximo el doble que la primera; anotar los resultados en specs/002-records-api/research.md R15 junto con el equipo en que se midió (procesador, memoria, sistema operativo); no ejecutar `make test` mientras el archivo grande esté cargado (sus IDs llegan a 990.010.000 y los tests suponen que no hay filas reales con ID ≥ 9.000.000); borrar el archivo grande y restaurar la base (`make db-reset && make load`)
- [X] T035 Ejecutar quickstart.md §1–§3 y §5 desde base limpia y comprobar cada valor esperado de la tabla; aquí es donde se verifican con los datos reales SC-001 (9.984 registros al recorrer el listado) y SC-007 (28 filas no cargadas y su conteo por motivo), que los tests automáticos cubren con filas sembradas; `docker compose stop db` + `uv run pytest tests/test_validation.py tests/test_filters.py` pasa sin base de datos; `docker compose start db`
- [X] T036 Cuando T032 y T034 tengan resultados, añadir a NOTES.md las secciones de la API, en inglés y breves: "Filtering and pagination" (filtros elegidos y por qué, cursor frente a `OFFSET` con las cifras de research R1, qué pasa con los datos que faltan, errores 422) y "Scale (API)" (índices y su costo de escritura, R7; peor caso y resumen, R9; conexiones, límites de tiempo y 503, R11; resultado de T034); actualizar en "Scale (loader)" el tiempo de carga con el índice nuevo y en "Schema" la línea de índices; "Lint exceptions" sin cambios salvo que T032 añada alguna

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)** → **Foundational (2)** → historias → **Error temporal (8)** → **Polish (9)**
- US1 (fase 3) crea los tres módulos; US2–US5 los amplían
- La fase 8 necesita `GET /records` (US1)

### User Story Dependencies

- **US1 (P1)**: tras la fase 2
- **US2 (P1)**: tras US1 (añade campos a `RecordFilters` y completa `build_where`)
- **US3 (P2)**: tras US2 (valida los rangos que US2 declara)
- **US4 (P3)**: tras US2 (reutiliza los filtros); no depende de US3
- **US5 (P3)**: tras la fase 2; solo comparte archivos con las demás, no lógica

### Within Each User Story

- Tests primero y en rojo; luego implementación hasta verde
- app/schemas.py y app/filters.py antes que app/records.py
- Un escritor por archivo: las tareas sobre tests/test_filters.py, tests/test_records_api.py, app/filters.py o app/records.py son secuenciales entre sí

### Parallel Opportunities

- T003, T005 y T006 en paralelo (archivos distintos), después de T002
- T021 (app/schemas.py) en paralelo con T019–T020
- T026 (app/schemas.py) en paralelo con T024–T025
- T033 en paralelo con T034–T035; T036 va al final porque usa los resultados de T032 y T034

---

## Parallel Example: Foundational

```text
Task: "T003: ajustes del pool en app/config.py y .env.example"
Task: "T005: Record y RecordPage en app/schemas.py"
Task: "T006: record_row y fixture seed en tests/conftest.py"
```

## Parallel Example: User Story 4

```text
Task: "T019–T020: casos de resumen en tests/test_filters.py y tests/test_records_api.py"
Task: "T021: SummaryRow en app/schemas.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1 y 2)

1. Fases 1 y 2
2. Fase 3 (US1): listado paginado
3. Fase 4 (US2): filtros — con esto se cubre lo que la prueba pide como mínimo
4. **Parar y validar** con quickstart.md §3 (filas de `/records`)

### Incremental Delivery

1. US1 → listado por páginas
2. US2 → filtros (mínimo entregable para la prueba)
3. US3 → errores completos, incluido el rango invertido
4. US4 → resumen (opcional)
5. US5 → filas no cargadas (opcional)
6. Fase 8 → error temporal
7. Polish → documentación, lint, medición de escala, quickstart y NOTES.md

### Commits sugeridos

Un commit por fase cerrada (`feat(002): ...`, `test(002): ...`), siempre con `make test` y
`make lint` en verde (constitución VI).

---

## Notes

- [P] = archivos distintos, sin dependencias pendientes
- Los tests de integración siembran sus filas (IDs ≥ 9.000.000, país `Testland` o `Testonia`,
  archivos `test_api_*.csv`) y el fixture `seed` borra solo las claves que insertó; nunca
  dependen de los datos cargados ni los modifican. Suponen que no hay filas reales con
  ID ≥ 9.000.000, lo que se cumple con el archivo actual (IDs hasta 10.000)
- Los valores de referencia de este archivo salen de la base cargada con el archivo actual; si
  un conteo del quickstart no coincide, se investiga antes de cambiar la expectativa
- `make db-reset` borra los datos: después hay que ejecutar `make load`

## Desviaciones al implementar

Lo que se hizo distinto de lo escrito arriba, y por qué:

- **T030** — un solo manejador, registrado para `psycopg.OperationalError`, en vez de tres:
  `PoolTimeout` y `QueryCanceled` son subclases suyas (comprobado), así que cubre los tres casos.
- **T010, T011** — el alias `Conn` (la dependencia de conexión) pasó de app/main.py a app/db.py,
  para que app/records.py lo use sin importar de `main`.
- **T010, T028** — app/records.py tiene dos funciones de apoyo, `_page` (separa la página de la
  fila extra y da la fila del cursor) y `_where` (antepone `WHERE` si hay condiciones), comunes
  a las dos rutas paginadas. Las rutas llevan además `tags` para agruparlas en `/docs`.
- **T006** — además de `record_row` hay un `reject_row` equivalente para `load_rejects`.
- **T008, T013** — las filas sembradas empiezan en el ID 9.000.001 y el cursor que las aísla es
  `after_id=9000000` (no 8.999.999); el efecto es el mismo.
- **T025** — se siembran 4 filas en `test_api_a.csv` (líneas 5, 9, 12 y 15) y 2 en
  `test_api_b.csv`, para que el conteo por motivo tenga un motivo repetido y la última página
  del recorrido con `limit=2` quede exactamente llena.
- **T034** — se midieron 12 peticiones, no solo las 4 de SC-003, para tener cifras reales del
  peor caso y del resumen (research R15).
- **T004, añadido después de implementar** — en la verificación manual, la primera petición tras
  reiniciar la base con el servicio en marcha devolvió 503 aunque la base ya estaba disponible.
  El pool comprueba ahora cada conexión antes de entregarla
  (`check=ConnectionPool.check_connection` en app/db.py) y hay un test que lo fija en
  tests/test_records_api.py. Cuesta unos 0,5 ms por petición (research R11).
