# Research: Cargador del dataset Coffee Health

**Feature**: 001-csv-loader | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

Formato de cada decisión: Decision / Rationale / Alternatives considered. Las cifras de
rendimiento son mediciones reales en el equipo de desarrollo, salvo donde se indica.

## R1. Motor de validación: modelo Pydantic con validadores previos

**Decision**: Un modelo Pydantic v2 `HealthRecord` (frozen). Cada campo es
`Annotated[...]` con un `BeforeValidator` propio que recorta, detecta faltantes, parsea de
forma estricta y normaliza. Los rangos se declaran con `Field(ge=, le=)` y los categóricos son
`StrEnum`. Los campos corregidos se registran en el contexto de validación:
`HealthRecord.model_validate(data, context={"fixed": set()})`, y cada validador añade
`info.field_name` cuando cambia el valor.

**Rationale**:
- Las reglas quedan declaradas en un solo lugar y los errores salen por columna
  (`ValidationError.errors()`) sin código propio de acumulación.
- La coerción por defecto de Pydantic es demasiado permisiva para un export sucio (acepta
  `" 40 "`, `"on"`/`"off"`/`"t"`/`"f"` como booleanos, `"1e3"`, `"NaN"` y `"Infinity"` como
  decimales). Por eso el parseo lo hacen validadores propios con
  `re.fullmatch(r"\d+")` y `re.fullmatch(r"\d+(\.\d+)?")`, y `Decimal`, nunca `float`.
  `str.isdigit()` no sirve: acepta dígitos no ASCII como `"４０"` o `"²"`.
- El contexto de validación permite saber qué columnas se corrigieron sin comparar después
  representaciones de texto (comparar `"3"` con `Decimal("3.0")` daría falsos "corregidos").

**Hallazgos del prototipo** (scratchpad, 2026-11-11, Pydantic 2.14.3):
1. `Annotated[Decimal | None, BeforeValidator(...), Field(ge=10)]` lanza `TypeError` (no
   `ValidationError`) cuando el valor es `None`: la restricción se aplica sobre la unión. Hay
   que poner la restricción dentro del tipo opcional:
   `Annotated[Annotated[Decimal, Field(ge=10, le=80)] | None, BeforeValidator(parse_decimal)]`.
2. Si un validador devuelve `None` en un campo obligatorio, Pydantic da un error genérico
   (`decimal_type`). Los campos obligatorios usan una variante del validador que lanza
   `PydanticCustomError("missing_required", ...)`.
3. Rendimiento: 51.620 filas/s validando las 16 columnas (un solo hilo). Muy por encima de
   lo que pide SC-006 (20.000 filas/s de extremo a extremo).

**Alternatives considered**:
- Funciones puras sin Pydantic: unas 2-3 veces más rápido, pero hay que reimplementar la
  acumulación de errores y los rangos. El usuario pidió Pydantic y la velocidad sobra.
- `TypeAdapter` sobre un `TypedDict`: mismo coste y menos legible que un modelo.
- Coerción por defecto (`strict=False` sin validadores propios): descartada por la lista de
  entradas que acepta indebidamente.

## R2. Códigos de motivo de rechazo

**Decision**: Un `StrEnum RejectReason` compartido por el validador, el loader y un `CHECK`
en la tabla:

| Código | Cuándo |
|---|---|
| `column_count` | La fila no tiene 16 campos |
| `missing_required` | Falta ID, Country, Coffee_Intake o Caffeine_mg |
| `not_numeric` | Entero o decimal que no cumple el formato estricto (`thirty`, `4O`, `-3`, `1e3`, `40.0` en un entero) |
| `out_of_range` | Número fuera del rango plausible (FR-014) |
| `unknown_category` | Categórico fuera de su catálogo tras normalizar |
| `unknown_country` | País fuera del catálogo tras normalizar |
| `invalid_boolean` | Booleano fuera de `0/1/y/yes/true/n/no/false` |
| `duplicate_exact` | Aparición anterior de un ID, idéntica a la conservada |
| `duplicate_conflict` | Aparición anterior de un ID, distinta de la conservada |

Fila con varias columnas inválidas: `reason`, `column_name` y `raw_value` son los de la
primera columna inválida en el orden de la cabecera; `detail` lista todas.

**Rationale**: códigos estables y consultables (`group by reason`), con un mapeo directo desde
los tipos de error de Pydantic (`greater_than_equal`/`less_than_equal` → `out_of_range`).

**Alternatives considered**: guardar el mensaje de error de Pydantic tal cual (no agrupable y
cambia entre versiones).

## R3. Tipos y restricciones del esquema

**Decision**:
- `id integer PRIMARY KEY` (el ID de origen, sin `serial`).
- `age smallint`, `heart_rate smallint`.
- `coffee_intake`, `sleep_hours`, `bmi`, `physical_activity_hours` `numeric(5,1)`;
  `caffeine_mg numeric(6,1)`.
- Categóricos `text` con `CHECK (col IN (...))`; `country text` con `CHECK (btrim(country) <> '')`.
- `smoking`, `alcohol_consumption` `boolean`.
- `NOT NULL` solo en `id`, `country`, `coffee_intake`, `caffeine_mg` (clarificación Q5 de la
  spec). El texto del `/speckit-plan` pedía "todo NOT NULL"; quedó superado por esa
  clarificación.
- `CHECK` de rango iguales a los del validador; un `CHECK` sobre `NULL` se evalúa como cierto,
  así que los opcionales vacíos pasan.
- `loaded_at timestamptz NOT NULL DEFAULT now()`, actualizado también en cada `UPDATE`.

**Rationale**:
- `numeric` es exacto: la precisión del origen es un decimal y los promedios no arrastran
  ruido binario. Coste: algo más lento que `real` en agregaciones, irrelevante aquí.
- `integer` llega a 2.147 millones de IDs: sobra para "decenas de millones". `bigint` duplica el
  tamaño de la PK sin necesidad.
- `text + CHECK` se puede cambiar con un `ALTER TABLE` simple, no exige registrar tipos en
  psycopg y se lee igual en SQL. Un `ENUM` ocupa 4 bytes y da orden natural, pero añadir o
  renombrar valores es más rígido. Una tabla catálogo con FK añade joins a cada consulta para
  catálogos de 3 a 5 valores.
- País sin `CHECK` de catálogo: es un catálogo abierto; ampliar países no debe exigir cambiar el
  esquema. El loader valida contra su catálogo.

**Alternatives considered**: `ENUM` nativos; tablas catálogo con FK; `real`/`double precision`;
`bigint`; `NOT NULL` en todas las columnas (descartado por Q5).

## R4. Rechazos en tabla, no en archivo

**Decision**: Tabla `load_rejects` con `PRIMARY KEY (source_file, line_number)`. Al empezar la
carga se borran las filas de ese `source_file` y se insertan las nuevas, todo en la misma
transacción. `source_file` es el nombre del archivo (`Path.name`).

**Rationale**: el CSV es formato de entrada, no de salida (decisión del usuario). Explicar una
fila no cargada pasa a ser un `SELECT`, y la API (feature 002) puede exponerla. Borrar y
reinsertar por archivo hace la recarga idempotente (Q4: sin historial). La PK impide duplicados
aunque haya un error de lógica.

**Alternatives considered**: CSV de rechazos regenerado en cada ejecución (no consultable);
historial con tabla de ejecuciones (descartado en Q4); usar la ruta completa como
`source_file` (la misma carga desde otra carpeta duplicaría los rechazos).

## R5. Sin ORM

**Decision**: SQL explícito con psycopg 3 y `psycopg.sql` para componer identificadores.

**Rationale**: el repo ya trae psycopg con pool y ningún ORM. La carga masiva necesita `COPY`,
que un ORM no expone. La validación vive en Pydantic y la segunda barrera en los `CHECK`.
Constitución V. Confirmado por el usuario.

**Alternatives considered**: SQLAlchemy Core / SQLModel (dependencia nueva y una capa más que
explicar, sin resolver nada que psycopg no resuelva).

## R6. Lectura en streaming y escritura con COPY a una tabla temporal

**Decision**:
1. `open(path, encoding="utf-8-sig", newline="")` + `csv.reader`; el número de línea es
   `reader.line_num` (línea física donde termina el registro; la cabecera es la línea 1).
   `utf-8-sig` quita el BOM; `newline=""` deja que `csv` maneje CRLF y comillas.
2. Se valida por bloques de `--chunk-size` filas (por defecto 50.000).
3. Por bloque, dos `COPY ... FROM STDIN` seguidos con `cursor.copy()` y `write_row()`: las
   válidas a `_staging` (tabla temporal `ON COMMIT DROP`, con `line_no`) y los rechazos a
   `load_rejects`. Dos `COPY` no pueden estar abiertos a la vez en la misma conexión; por eso
   se acumula un bloque.

**Rationale**: la memoria depende del tamaño del bloque, no del archivo (FR-028). `COPY` es la
vía de ingesta más rápida de Postgres (un viaje de red por bloque, sin parseo de SQL por fila).
Las tablas temporales no escriben WAL.

**Alternatives considered**: `executemany` de `INSERT` (de 10 a 50 veces más lento a escala);
`psql \copy` (prohibido por la prueba); cargar todo el archivo en memoria (incumple FR-028).

## R7. Duplicados resueltos en SQL, no en memoria

**Decision**: tras cargar `_staging`:
`row_number() OVER (PARTITION BY id ORDER BY line_no DESC)`. La fila 1 de cada ID es la
ganadora. Las demás se insertan en `load_rejects` con un `INSERT ... SELECT`. Son
`duplicate_exact` si `(cols de la perdedora) IS NOT DISTINCT FROM (cols de la ganadora)`
(comparación segura con `NULL`), o `duplicate_conflict` en caso contrario. `detail` incluye
la línea de la ganadora.

**Rationale**: un `set`/`dict` de IDs en Python crece con el archivo (decenas de millones de
enteros son cientos de MB). Postgres ordena en disco si hace falta (`work_mem` y archivos
temporales). Además, la regla "gana la última" necesita ver todas las apariciones, que pueden
caer en bloques distintos.

**Alternatives considered**: dict en memoria con la última línea por ID (no escala); exigir el
CSV ordenado por ID (no controlamos el origen).

## R8. Upsert: MERGE con RETURNING merge_action()

**Decision**: Postgres 17:

```sql
MERGE INTO health_records t
USING (ganadoras de _staging) s ON t.id = s.id
WHEN MATCHED AND (t.age, t.gender, ...) IS DISTINCT FROM (s.age, s.gender, ...)
  THEN UPDATE SET ..., loaded_at = now()
WHEN NOT MATCHED THEN INSERT (...) VALUES (...)
RETURNING merge_action();
```

Se cuentan `INSERT` y `UPDATE`; sin cambios = ganadoras − insertadas − actualizadas.

Verificado en el Postgres 17 del proyecto (2026-11-11): `MERGE` no puede ir como subconsulta en
`FROM` (error de sintaxis), pero sí como CTE:
`WITH m AS (MERGE ... RETURNING merge_action() AS action) SELECT action, count(*) FROM m GROUP BY 1`.
Con origen `{1: igual, 2: distinto, 3: nuevo}` devolvió `INSERT 1`, `UPDATE 1`, y la fila igual no
se tocó.

**Rationale**: `merge_action()` es la forma documentada de saber qué hizo cada fila. La
alternativa con `INSERT ... ON CONFLICT ... RETURNING (xmax = 0)` depende de un detalle interno
de MVCC sin garantía. `IS DISTINCT FROM` evita reescribir filas idénticas (FR-022): sin
escrituras inútiles, sin WAL extra y sin tocar `loaded_at`.

**Riesgo y mitigación**: `MERGE` no es seguro frente a inserciones concurrentes del mismo ID
(puede fallar por clave duplicada, cosa que `ON CONFLICT` sí resuelve). Se elimina con R10 (una
carga a la vez), que la spec ya asume.

**Alternatives considered**: `INSERT ... ON CONFLICT DO UPDATE ... WHERE ... IS DISTINCT FROM`
con `RETURNING (xmax = 0)` (válida, pero depende de internals); `DELETE` seguido de `INSERT`
(reescribe todo, no distingue insertadas de actualizadas).

## R9. Una sola transacción

**Decision**: toda la carga en una transacción: bloqueo, borrado de rechazos previos, `COPY`,
deduplicación, `MERGE` y verificación de invariantes. `load_csv()` no hace commit; lo hace
`main()` solo si todo cuadra.

**Rationale**: FR-023 y constitución III. La transacción larga es aceptable para un proceso por
lotes que corre solo (R10). Con decenas de millones de filas el coste es espacio temporal para
`_staging` (aproximadamente el tamaño del CSV) y WAL del `MERGE`, no memoria.

**Alternatives considered**: commits por bloque (más rápido de reanudar y con transacciones
cortas, pero una carga fallida deja la base a medias e incumple FR-023). Para cargas iniciales
de cientos de millones de filas sí se elegiría esto, con una tabla de ejecuciones para
reanudar. Queda documentado como evolución.

## R10. Una carga a la vez

**Decision**: `SELECT pg_advisory_xact_lock(<constante>)` al inicio de la transacción. Una
segunda carga espera a que termine la primera; el bloqueo se libera solo al confirmar o
deshacer.

**Rationale**: hace cierta la suposición de la spec y protege a `MERGE` (R8). Una línea de
código.

**Alternatives considered**: `LOCK TABLE health_records` (bloquea también a los lectores de la
API).

## R11. Importar `app.*` desde `scripts/load_data.py`

**Decision**: al inicio del script, añadir la raíz del repo a `sys.path`:
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`, seguido de los imports de
`app` con `# noqa: E402`. La lógica vive en `load_csv()` y en funciones importables desde los
tests (`from scripts.load_data import load_csv`; pytest ya tiene `pythonpath = ["."]`).

**Rationale**: `make load` ejecuta `python scripts/load_data.py`, que pone `scripts/` (no la
raíz) en `sys.path`. El contrato del Makefile no se cambia (constitución V). Es la única
excepción de lint prevista y se lista en NOTES.md (constitución VI).

**Alternatives considered**: `python -m scripts.load_data` (exige cambiar el Makefile);
convertir el proyecto en paquete instalable (`[tool.uv] package = false` en el repo; cambiarlo
es más intrusivo).

## R12. Conexión del loader

**Decision**: conexión directa (`psycopg.connect`, sin pool) con
`DATABASE_URL` de `app.config.settings` y `connect_timeout=10`.

**Rationale**: el loader es un proceso único con una sola conexión; un pool no aporta nada. El
timeout convierte un fallo de red en un error claro en segundos. Caso real en este equipo:
`localhost` resuelve primero a `::1` y el puerto IPv6 de Docker acepta la conexión pero no
responde, así que sin timeout el proceso se cuelga. Se resolvió con `127.0.0.1` en `.env`.

## R13. Validación de cabecera y códigos de salida

**Decision**: la cabecera (sin BOM) debe ser exactamente la lista de 16 nombres en orden; si no,
se detiene antes de leer datos y muestra lo esperado y lo encontrado. Códigos de salida: `0`
éxito (aunque haya rechazos); `1` error fatal (archivo, cabecera, conexión, invariantes);
`2` argumentos inválidos (lo emite argparse).

**Rationale**: FR-002 y FR-027. Exigir el orden evita mapear columnas por nombre con
ambigüedades. Si el origen cambiara de orden, se adaptaría con un `DictReader`.

## R14. Índices

**Decision**: en esta feature solo las PK de `health_records (id)` y `load_rejects
(source_file, line_number)`. Los índices para filtros los decide la feature 002 con `EXPLAIN`.

**Rationale**: constitución IV, cada índice se justifica con una consulta. Para una carga
inicial masiva conviene crear los índices secundarios después del `COPY`: mantenerlos fila a
fila durante la carga cuesta más que construirlos de una vez. En recargas incrementales se
mantienen.

## R15. Medición de escala

**Decision**: script de apoyo `scripts/make_big_csv.py` que replica el CSV real N veces
desplazando los IDs (`id + k * 10.000.000`), conservando la suciedad del original. Se mide con
1 millón de filas: tiempo total, filas/s y memoria pico del proceso
(`psutil` no está disponible; se usa `tracemalloc` para el pico de memoria de Python y el
Administrador de tareas o `/usr/bin/time -v` como referencia).

**Estimación previa** (no medida): validación a ~51.600 filas/s, más `csv` y `COPY` →
30.000-40.000 filas/s de extremo a extremo. 10 millones de filas tardarían entre 4 y 6 minutos
más el `MERGE`. Los valores reales se escriben aquí y en NOTES.md tras medir.

**Alternatives considered**: validación en paralelo con `multiprocessing` (complica el orden de
líneas y la atomicidad; no hace falta para el objetivo).

## Resumen de NEEDS CLARIFICATION

Ninguno: el contexto técnico venía fijado por el repo y la constitución, y las decisiones de
negocio se cerraron en `/speckit-clarify`.
