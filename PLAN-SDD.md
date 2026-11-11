# Plan SDD — Medallo take-home (Python + FastAPI + Postgres)

Prueba: <https://github.com/medallo-dev/python-fastapi-take-home>
Método: Spec-Driven Development con spec-kit 1.4.1 (skills `speckit-*` ya instaladas en `.claude/skills/`).

Ciclo por feature: `constitution` (una vez) → `specify` → `clarify` → `plan` → `tasks` → `analyze` → `implement` → `converge`.
Regla del método: en `specify` va el **qué y por qué** (sin tecnología); en `plan` va el **cómo**.

---

## 0. Qué pide la prueba (resumen)

| Parte | Entregable |
|---|---|
| 1. Loader | Esquema SQL en `db/init/` + `scripts/load_data.py`: valida, normaliza, no se detiene ante filas malas, reporta rechazos con línea y motivo, idempotente, resumen final |
| 2. API | Endpoint(s) con filtros, paginación obligatoria, modelos Pydantic, 422 claros; resumen agregado opcional |
| 3. Calidad | Tests de validación **sin BD**, ≥1 test de API con `TestClient` **contra la BD real**, type hints, `make lint` (ruff + mypy strict) |
| 4. Escala | Razonar para decenas de millones de filas: memoria del loader, índices, paginación profunda, conexiones |
| Entrega | `make db-reset && make load && make dev` funciona, `make test` pasa, `NOTES.md` corto |

## 1. Lo que encontré al perfilar el CSV

Esto alimenta la spec: son hechos medidos sobre `data/coffee_health_raw.csv`, no supuestos.

| Hallazgo | Detalle |
|---|---|
| Formato | BOM UTF-8, finales CRLF, 10 filas con todos los campos entre comillas (CSV válido) |
| Líneas | 10.012 filas de datos + 3 líneas en blanco (5953, 10015, 10016) |
| Columnas incorrectas | 3 filas: línea 2659 (17 campos, sobra `extra`), 4869 y 7175 (13 campos, truncadas) |
| Espacios y tabs | 25 filas con valores como `' Germany  '`, `'\tUSA '`, `'  Female  '` |
| Mayúsculas | 25 filas: `SWEDEN`, `belgium`, `MALE`, `GOOD`, `low`… |
| Booleanos | Corregibles: `Y` ×2, `no`, `yes` ×2, `false`. Fuera de dominio: `-1`, `2` |
| Age inválido | `thirty`, `4O` (letra O), `unknown`, vacío, `-3`, `150`, `212` |
| Sleep_Hours inválido | `seven`, vacío, `-1`, `25.0` |
| BMI inválido | `n/a`, vacío, `0` |
| Heart_Rate inválido | vacío, `0`, `350` |
| Gender inválido | vacío, `?`, `N/A` |
| IDs repetidos | 12: 8 duplicados exactos y 4 en conflicto (IDs 3338, 6115, 6748, 7494; difieren en Age y Heart_Rate) |
| Rangos reales de los datos sanos | Age 18–80, café 0–8.2, cafeína 0–780.3, sueño 3.0–10.0, BMI 15.0–38.2, pulso 50–109, actividad 0–15 |

**Resultado esperado con las reglas que propongo** (lo simulé con un script):

| Categoría | Filas |
|---|---|
| Filas de datos leídas | 10.012 (+3 en blanco omitidas) |
| Rechazadas | 25 (3 columnas, 7 Age, 4 Sleep_Hours, 3 Gender, 3 BMI, 3 Heart_Rate, 2 Smoking) |
| Duplicadas | 12 (8 exactas, 4 en conflicto) |
| Corregidas | 56 (25 espacios, 25 mayúsculas, 6 booleanos) |
| **Cargadas** | **9.975** |

Conciliación: 10.012 = 25 + 12 + 9.975. Esos números son el criterio de aceptación del loader. Si cambias una regla (por ejemplo, aceptar `4O` como 40), cambian.

## 2. Decisiones que propongo

Tú las confirmas en `/speckit-clarify`; aquí va mi recomendación y el porqué, que es lo que te van a preguntar.

### Arquitectura

Monolito pequeño por capas, con un núcleo puro y la entrada/salida en los bordes. Dos puntos de entrada comparten las mismas reglas: el loader (escritura por lotes) y la API (lectura).

```
CSV ──► scripts/load_data.py ──► Postgres ◄── app/records.py ◄── /docs o React
        (lee por bloques,        (staging, dedupe,   (filtros, keyset)
         COPY)                    upsert, CHECK)
              │                                          │
              └────────► núcleo puro, sin I/O ◄──────────┘
                 app/validation.py · app/filters.py · app/schemas.py
```

| Capa | Archivos | Responsabilidad |
|---|---|---|
| Núcleo puro | `app/validation.py`, `app/filters.py`, `app/schemas.py` | Reglas, normalización, modelos Pydantic, construcción del SQL. No toca archivos, red ni base |
| Bordes de I/O | `scripts/load_data.py`, `app/records.py`, `app/db.py`, `app/config.py` | Leer el CSV, atender HTTP, hablar con Postgres |
| Base de datos | `db/init/*.sql` | Guardar, deduplicar y hacer el upsert en bloque; los `CHECK` son la última barrera |
| Cliente (opcional) | `frontend/` | Consume la API por HTTP; no comparte código con el backend |

Por qué esta y no otra:

- **La prueba exige tests de validación sin base de datos.** Con las reglas en funciones puras eso sale solo, sin mocks.
- **Respeta la estructura del repo** (`app/`, `scripts/`, `tests/`, `db/init/`): quien revisa encuentra cada cosa donde la espera.
- **Escritura y lectura tienen necesidades opuestas.** La carga es un proceso único y masivo (`COPY`, una transacción); la API son muchas lecturas pequeñas y concurrentes (pool, keyset). Separarlas en dos puntos de entrada deja optimizar cada una sin duplicar las reglas.
- **Escala sin cambiar de diseño.** Python mantiene memoria constante y el trabajo pesado lo hace Postgres.
- **Es lo mínimo que cumple.** Una tabla, un proceso que escribe y una API de solo lectura no justifican arquitectura hexagonal, repositorios, ORM, colas ni microservicios; serían capas extra que explicar sin resolver nada.

Cuándo la cambiaría: si aparecieran endpoints de escritura o lógica de negocio, añadiría una capa de servicios; con varias fuentes de datos o cargas concurrentes, una cola con workers; con mucha más lectura, réplicas o PgBouncer.

### Datos y loader

| Tema | Propuesta | Por qué |
|---|---|---|
| Valores faltantes (`''`, `n/a`, `?`, `unknown`) | Rechazar la fila | Las 16 columnas son obligatorias; cargar NULL complica filtros y promedios. Son 12 filas de 10.012 |
| Números con texto (`thirty`, `seven`, `4O`) | Rechazar, no adivinar | Corregir `4O`→40 es inventar un dato de salud. Solo se corrige formato inequívoco |
| Rangos plausibles (inclusive) | Age 18–120 · café 0–20 · cafeína 0–2000 · sueño 1–16 · BMI 10–80 · pulso 30–200 · actividad 0–80 | Holgados frente a lo observado; solo dejan fuera lo físicamente imposible. Un único módulo de constantes |
| Fila con 17 campos | Rechazar | Si sobra un campo no se puede confiar en la alineación de las columnas |
| Duplicados por ID | Gana la **última** aparición válida; las anteriores se reportan como `duplicate_exact` o `duplicate_conflict` con la línea que las reemplazó | Misma regla dentro del archivo y entre recargas (un export más nuevo actualiza). Determinista y explicable |
| País | Catálogo de los 20 países conocidos, comparación sin mayúsculas; desconocido → rechazo | `.title()` rompería `USA` y `UK`. Aceptar cualquier texto dejaría entrar `Germny` como país nuevo |
| Booleanos | `0/1`; se corrigen `y/yes/true` y `n/no/false`; lo demás se rechaza | `-1` y `2` no tienen interpretación segura |
| Decimales con más de 1 cifra | Redondear a 1 decimal y contar como corregida | No ocurre en el archivo; evita que Postgres redondee en silencio |
| Validación cruzada (cafeína vs tazas) | No hacerla | La relación medida va de 49 a 140 mg por taza: no hay un umbral defendible |
| Rechazos | Tabla `load_rejects` (archivo, línea, motivo, columna, valor, fila original), reemplazada en cada ejecución dentro de la misma transacción + conteo por motivo en consola | Consultable con SQL y desde la API: "explicar cada fila no cargada" es un `select`. Reemplazar por archivo de origen la hace idempotente |
| Motor de validación | Un modelo Pydantic por fila (`Field(ge=, le=)`, enums, validadores previos que normalizan) | Reglas declarativas en un solo lugar y errores por columna sin código extra. Números y booleanos se parsean con validadores propios y estrictos: por defecto Pydantic acepta cosas que no queremos (`on`/`off` como booleanos) |
| ORM | No añadir; psycopg + Pydantic | El repo ya trae psycopg con pool; la carga masiva necesita `COPY`, que un ORM no hace; la API son pocas consultas de solo lectura. Las reglas viven en Pydantic (aplicación) y en los `CHECK` (base) |
| Idempotencia | Upsert por `id` que solo actualiza si la fila cambió | La segunda ejecución reporta 0 insertadas y 0 actualizadas |
| Atomicidad | Toda la carga en una transacción | Un fallo a mitad no deja la tabla a medias |

### Esquema

| Tema | Propuesta | Por qué |
|---|---|---|
| Clave | `id integer PRIMARY KEY` (el ID de origen) | Es la identidad natural y lo que hace posible el upsert. `integer` llega a 2.100 millones |
| Enteros | `age smallint`, `heart_rate smallint` | 2 bytes; el rango cabe de sobra |
| Decimales | `numeric(5,1)` (`caffeine_mg numeric(6,1)`) | Exacto, igual a la precisión del origen; rangos y promedios sin ruido de coma flotante |
| Categóricos | `text` + `CHECK (col in (...))` | Más fácil de evolucionar que un `ENUM` nativo y sin registrar tipos en psycopg. Trade-off: ENUM ocupa 4 bytes y da orden natural |
| País | `text NOT NULL` sin CHECK | Catálogo abierto: se valida en el loader |
| Restricciones | Todo `NOT NULL` + `CHECK` de rango iguales a los del validador | Defensa en profundidad: la BD es la última barrera |
| Auditoría | `loaded_at timestamptz default now()` | Saber cuándo entró cada fila |
| Rechazos | `load_rejects(source_file, line_number, reason, column_name, raw_value, detail, raw_fields text[], loaded_at)` con PK `(source_file, line_number)` | Una fila por línea no cargada; la PK impide duplicarla |

### API

| Tema | Propuesta | Por qué |
|---|---|---|
| Paginación | **Keyset** por `id`: `?limit=50&after_id=123`, respuesta `{items, next_after_id, limit}` | `OFFSET` lee y descarta todas las filas anteriores: la página 50.000 cuesta 50.000 veces más. Keyset cuesta lo mismo en cualquier página |
| Tamaño de página | Por defecto 50, máximo 200 | Acota el costo por petición |
| Total de coincidencias | No se devuelve en el listado; lo da el endpoint de resumen | `count(*)` con filtros recorre todas las coincidencias en cada página |
| Filtros | Multiselección: `country`, `gender`, `sleep_quality`, `stress_level` · booleano: `smoking` · rangos: `min/max_age`, `min/max_coffee_intake`, `min/max_bmi` | "Unos pocos filtros útiles"; cada filtro extra es una decisión de índice |
| Validación | Modelo Pydantic como query params, `extra="forbid"`, validador `min ≤ max` | FastAPI devuelve el 422 solo; un parámetro mal escrito (`min_agee`) no se ignora en silencio |
| País desconocido | 200 con página vacía | No es un enum cerrado para el cliente |
| Números en JSON | `float` en el modelo de respuesta | Pydantic v2 serializa `Decimal` como cadena; el cliente espera números |
| SQL | `psycopg.sql` + parámetros, `= ANY(%s)` para multiselección, función pura `build_where` | Sin inyección y testeable sin BD |
| Índices | PK `(id)` + `(country, id)` | La PK sirve el listado y los filtros poco selectivos (género, estrés, etc.). `(country, id)` sirve el filtro selectivo más probable sin ordenar. Cada índice extra encarece la carga |
| Conexiones | Endpoints `def` + pool acotado, tamaño y timeouts en `Settings`, `statement_timeout`, `PoolTimeout` → 503 | Bajo saturación responde un error temporal en vez de colgarse. Regla: workers × `max_size` < `max_connections` |
| Resumen (opcional) | `GET /records/summary?group_by=country\|gender\|sleep_quality\|stress_level\|coffee_cups` con los mismos filtros | Reutiliza `build_where`; devuelve conteo y promedios |
| Rechazos (opcional) | `GET /rejects` paginado con filtro por motivo y archivo, y `GET /rejects/summary` con el conteo por motivo | La calidad del dato se ve sin entrar a psql |

### Estructura de archivos resultante

```
app/validation.py        lógica pura: modelo Pydantic por fila, límites, catálogos, parse_row()
app/schemas.py           modelos Pydantic de respuesta
app/filters.py           RecordFilters + build_where() (puro)
app/records.py           router: /records, /records/summary, /rejects, /rejects/summary
scripts/load_data.py     CLI: lectura por bloques → COPY a staging y a load_rejects → dedupe SQL → upsert → resumen
db/init/002_health_records.sql   tablas health_records y load_rejects + checks
db/init/003_indexes.sql          índice (country, id)
tests/test_validation.py         sin BD
tests/test_filters.py            sin BD
tests/test_load_data.py          con BD (transacción + rollback)
tests/test_records_api.py        con BD (TestClient)
NOTES.md
specs/001-…/  specs/002-…/       artefactos SDD
frontend/                        (opcional, paso 7) Vite + React
```

Un detalle que rompe la carga si no se prevé: `python scripts/load_data.py` pone `scripts/` en `sys.path`, no la raíz, así que `import app.…` falla. Propongo añadir la raíz a `sys.path` al inicio del script y no tocar el Makefile.

---

## 3. Paso a paso

Comandos en PowerShell desde `c:\Users\Gmkras\Documents\prueba_medallo_dev`. Los `/speckit-…` se escriben en Claude Code. Los artefactos quedan en archivos, así que puedes usar `/clear` entre fases.

### Paso 1 — Fork y traer el repo a esta carpeta

La carpeta ya tiene `.claude/` y `.specify/` y aún no es un repo git. No chocan con ningún archivo del repo de la prueba.

```powershell
gh repo fork medallo-dev/python-fastapi-take-home --clone=false
git init -b main
git remote add origin https://github.com/Gmkras/python-fastapi-take-home.git
git remote add upstream https://github.com/medallo-dev/python-fastapi-take-home.git
git pull origin main
git branch -u origin/main
git add .claude .specify PLAN-SDD.md
git commit -m "chore: add spec-kit (SDD) scaffolding and plan"
```

El fork de un repo público es público: otros candidatos pueden verlo. Si prefieres privado, crea un repo privado y sube ahí el código.

### Paso 2 — Comprobar que la base funciona antes de tocar nada

`make` no está instalado en este PC. Usa los equivalentes o instala make (por ejemplo `winget install ezwinports.make`) y ejecútalo desde Git Bash. Quien revise sí usará `make`, así que conviene probarlo así al final.

| make | Equivalente en PowerShell |
|---|---|
| `make setup` | `uv sync; if (-not (Test-Path .env)) { Copy-Item .env.example .env }` |
| `make db-up` | `docker compose up -d --wait db` |
| `make db-reset` | `docker compose down -v; docker compose up -d --wait db` |
| `make load` | `uv run python scripts/load_data.py data/coffee_health_raw.csv` |
| `make dev` | `uv run fastapi dev app/main.py` |
| `make test` | `uv run pytest` |
| `make lint` | `uv run ruff check .; uv run ruff format --check .; uv run mypy` |

```powershell
uv sync; if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d --wait db
uv run pytest          # esperado: 2 passed
uv run ruff check .; uv run mypy
```

Los puertos 5433 y 8000 están libres hoy en tu Docker. `docker compose down -v` solo borra el volumen de este proyecto; no afecta a los otros contenedores.

### Paso 3 — Constitución (una sola vez)

```
/speckit-constitution Crea la constitución de este proyecto (prueba técnica "Coffee Health": loader CSV → Postgres + API FastAPI de solo lectura). Principios:
1. Correctitud de datos primero: ningún registro llega a la base sin ser parseado, validado y normalizado; cada fila no cargada debe poder explicarse (número de línea + motivo) y los conteos deben conciliar (leídas = cargadas + rechazadas + duplicadas).
2. Lógica pura y testeable: validación, normalización y construcción de consultas son funciones puras sin I/O, con pruebas pytest que corren sin base de datos. Para esa lógica se escriben primero las pruebas (TDD).
3. Idempotencia y atomicidad: toda carga puede repetirse sin duplicar ni corromper datos; una carga fallida no deja la base a medias.
4. Diseñado para escala, sin especular: memoria constante en el loader, operaciones por lotes y set-based en Postgres, paginación que no se degrada con la profundidad, índices justificados por una consulta concreta y por su costo de escritura. Ninguna optimización sin un motivo medible.
5. Simplicidad: stack fijo del repo (Python 3.12+, FastAPI, psycopg 3 con pool, Pydantic v2, Postgres 17, uv). SQL explícito y parametrizado, sin ORM. No se agregan dependencias sin justificación escrita. No se cambia el contrato del Makefile (make db-reset, load, dev, test, lint).
6. Calidad verificable: type hints en todo; ruff check, ruff format --check y mypy strict pasan; una tarea no está terminada hasta que las pruebas y el lint están en verde.
7. Decisiones documentadas: toda decisión ante un caso ambiguo (límites numéricos, duplicados, valores faltantes) queda escrita con su porqué en la spec y resumida en NOTES.md.
Convenciones: código, identificadores, commits y NOTES.md en inglés; specs en español.
```

```powershell
git add -A; git commit -m "docs: add project constitution"
```

### Paso 4 — Feature 001: loader

```powershell
git switch -c 001-csv-loader
```

**4.1 Especificar**

```
/speckit-specify Cargador del dataset "coffee health". Quiero un proceso que importe data/coffee_health_raw.csv (unos 10.000 registros exportados de otro sistema, NO limpios) a la base de datos, dejando solo registros válidos y normalizados, y que explique cada fila que no cargó.

Columnas esperadas (16, todas obligatorias): ID (entero, identificador único de persona), Age (entero, adultos), Gender (Male|Female|Other), Country (nombre de país), Coffee_Intake (decimal, tazas/día), Caffeine_mg (decimal, mg/día), Sleep_Hours (decimal, horas/noche), Sleep_Quality (Poor|Fair|Good|Excellent), BMI (decimal), Heart_Rate (entero, ppm en reposo), Stress_Level (Low|Medium|High), Physical_Activity_Hours (decimal, horas/semana), Health_Issues (None|Mild|Moderate|Severe), Occupation (Office|Healthcare|Service|Student|Other), Smoking (booleano 0/1), Alcohol_Consumption (booleano 0/1).

Problemas reales observados en el archivo: BOM UTF-8 y finales CRLF; 10 filas con todos los campos entre comillas; 3 líneas en blanco; 3 filas con número de columnas incorrecto (13 o 17); espacios o tabs alrededor de valores y variantes de mayúsculas en categóricos y países (' Germany  ', 'SWEDEN', 'male', 'GOOD'); booleanos como 'Y', 'yes', 'no', 'false' y fuera de dominio ('-1', '2'); numéricos vacíos o con texto ('', 'n/a', 'unknown', 'thirty', 'seven', '4O'); valores físicamente imposibles (Age -3/150/212, Sleep_Hours -1/25, BMI 0, Heart_Rate 0/350); marcadores de faltante en Gender ('', '?', 'N/A'); 12 IDs repetidos: 8 duplicados exactos y 4 con valores distintos (difieren en Age y Heart_Rate).

Reglas:
- Corregible (se carga y cuenta como "corregida"): espacios o tabs sobrantes; mayúsculas/minúsculas de categóricos y de países conocidos (sin romper 'USA' ni 'UK'); sinónimos booleanos inequívocos (y/yes/true → 1, n/no/false → 0); decimales con más de una cifra se redondean a un decimal.
- Inválido (se rechaza con número de línea, columna, valor y motivo): número de columnas incorrecto; valor obligatorio vacío o marcador de faltante; numérico no parseable (no se adivina: 'thirty' y '4O' se rechazan); valor fuera del rango plausible; categoría o país desconocido; booleano fuera de dominio.
- Rangos plausibles (inclusive): Age 18–120; Coffee_Intake 0–20; Caffeine_mg 0–2000; Sleep_Hours 1–16; BMI 10–80; Heart_Rate 30–200; Physical_Activity_Hours 0–80.
- Duplicados por ID: gana la última aparición válida en el archivo (la misma regla que al recargar un archivo más nuevo). Las anteriores no se cargan y se reportan como "duplicado exacto" o "duplicado en conflicto", indicando la línea que las reemplazó.
- Las líneas en blanco se omiten y se cuentan aparte; no son rechazos.
- Una cabecera que no coincide con las 16 columnas esperadas detiene el proceso con un error claro antes de leer datos.
- Una fila inválida nunca detiene la carga.
- Idempotencia: ejecutar la carga dos veces deja exactamente los mismos datos; la segunda ejecución reporta 0 insertadas y 0 actualizadas. Recargar un archivo con cambios actualiza los registros existentes por ID.
- Atomicidad: si la carga falla a mitad, la base queda como estaba.
- Reporte de rechazos: cada fila no cargada queda registrada en la base de datos (archivo de origen, línea, motivo, columna, valor, fila original) y se puede consultar. Volver a cargar el mismo archivo reemplaza sus rechazos; no los duplica.
- Resumen final en consola: filas leídas, en blanco, válidas, corregidas, rechazadas (por motivo), duplicadas (exactas / en conflicto), insertadas, actualizadas, sin cambios y total en la tabla. El proceso verifica que leídas = rechazadas + duplicadas + cargadas y que el total de la tabla coincide; si no, termina con error.
- Escala: debe poder procesar un archivo de decenas de millones de filas que no cabe en memoria.

Criterio de éxito con el archivo actual: 10.012 filas de datos leídas (más 3 en blanco), 25 rechazadas (3 por columnas, 7 Age, 4 Sleep_Hours, 3 Gender, 3 BMI, 3 Heart_Rate, 2 Smoking), 12 duplicadas (8 exactas, 4 en conflicto), 56 corregidas y 9.975 registros en la base.

Fuera de alcance: la API (otra feature), validaciones cruzadas entre columnas, imputación de valores faltantes.
```

Lee `specs/001-*/spec.md` completo antes de seguir: es lo que vas a defender.

**4.2 Aclarar** — `/speckit-clarify`. Hará hasta 5 preguntas; responde con la tabla de la sección 2. Si no pregunta algo que te importa, dilo: «registra también que la fila con 17 campos se rechaza».

**4.3 Planear**

```
/speckit-plan Stack fijo del repo: Python 3.12+, psycopg 3 (binary, pool), Pydantic v2, Postgres 17 en Docker, uv, pytest, ruff, mypy strict. Sin dependencias nuevas.

Estructura:
- app/validation.py: lógica PURA (sin I/O ni psycopg). Un modelo Pydantic HealthRecord con las reglas declaradas (Field(ge=, le=) para rangos, StrEnum para categóricos) y validadores previos (BeforeValidator) que normalizan: recortar espacios, llevar categóricos y países a su forma canónica, sinónimos booleanos. Enteros y decimales se parsean con validadores propios y estrictos (regex ^\d+$ y ^\d+(\.\d+)?$, Decimal y nunca float), sin depender de la coerción por defecto de Pydantic: se rechazan NaN, Infinity, notación científica, signos, guiones bajos, dígitos no ASCII y booleanos como on/off/t/f. Límites y catálogos en un solo lugar. parse_row(line_no, fields) -> HealthRecord | Rejection envuelve model_validate y convierte ValidationError.errors() en un rechazo que nombra todas las columnas inválidas (el motivo principal es la primera). "Corregida" se calcula comparando los valores crudos con los normalizados.
- scripts/load_data.py: CLI con argparse (csv_path, --chunk-size). load_csv(conn, csv_path) -> LoadSummary NO hace commit (lo hace main()), para poder probarlo en una transacción con rollback. Como "python scripts/load_data.py" deja scripts/ y no la raíz en sys.path, añadir la raíz del repo a sys.path al inicio del script para importar app.* sin cambiar el Makefile. DATABASE_URL sale de app.config.settings.
- db/init/002_health_records.sql: tablas health_records y load_rejects. Se conserva 001_notes.sql.

Esquema: id integer PRIMARY KEY (ID de origen, no serial); age y heart_rate smallint; decimales numeric(5,1) y caffeine_mg numeric(6,1); categóricos text con CHECK in (...); country text sin CHECK (se valida en el loader); smoking y alcohol_consumption boolean; loaded_at timestamptz default now(); todo NOT NULL; CHECK de rango iguales a los del validador. Solo el índice de la PK en esta feature. Tabla load_rejects(source_file text, line_number integer, reason text, column_name text, raw_value text, detail text, raw_fields text[], loaded_at timestamptz default now()) con PRIMARY KEY (source_file, line_number). Documentar en research.md: text+CHECK vs ENUM nativo vs tabla catálogo; numeric vs real; integer vs bigint; rechazos en tabla vs en archivo; por qué no ORM.

Flujo del loader, con memoria constante:
1. Abrir el CSV con encoding utf-8-sig y newline="" y recorrerlo con csv.reader; número de línea = reader.line_num. Validar la cabecera. Borrar de load_rejects los rechazos previos de ese source_file.
2. Leer y validar por bloques de --chunk-size filas (por defecto 50.000): la memoria depende del bloque, no del archivo.
3. Por cada bloque, dos COPY ... FROM STDIN seguidos (cursor.copy y write_row): las filas válidas a una tabla temporal de staging (ON COMMIT DROP) que incluye line_no, y los rechazos a load_rejects.
4. Duplicados resueltos en SQL sobre staging: row_number() over (partition by id order by line_no desc). Las perdedoras se clasifican como exactas o en conflicto comparándolas con la ganadora y se insertan en load_rejects con un INSERT ... SELECT.
5. Upsert de ganadoras: INSERT ... SELECT ... ON CONFLICT (id) DO UPDATE ... WHERE la fila existente IS DISTINCT FROM excluded, con RETURNING (xmax = 0) para contar insertadas y actualizadas; sin cambios = ganadoras menos devueltas. Evaluar en research.md la alternativa MERGE ... RETURNING merge_action() de Postgres 17.
6. Verificar invariantes, imprimir el resumen y hacer commit. Código de salida distinto de 0 si un invariante falla.
Todo en una sola transacción.

Pruebas:
- tests/test_validation.py: sin base de datos, parametrizadas, escritas antes que la implementación. Debe pasar con Docker apagado.
- tests/test_load_data.py: integración contra la BD real con CSV pequeños en tests/data/ con IDs >= 9.000.000, dentro de una transacción con rollback.

Escala (research.md): throughput medido en filas/s con un archivo sintético grande generado replicando el CSV con IDs desplazados; transacción única vs commits por lotes; por qué staging + SQL y no un set de IDs en memoria; crear índices secundarios después de una carga inicial masiva.
```

Revisa `plan.md`, `research.md` y `data-model.md`.

**4.4 Tareas, análisis, implementación**

```
/speckit-tasks
/speckit-analyze
/speckit-implement
```

- En `tasks.md` comprueba que las tareas de tests de validación van **antes** que las de implementación.
- `/speckit-analyze` no modifica nada: reporta incoherencias entre spec, plan y tareas. Corrige lo que marque como CRITICAL antes de implementar.
- Si `/speckit-implement` se queda corto, `/speckit-converge` compara el código con la spec y añade las tareas que falten.

**4.5 Verificar**

```powershell
docker compose down -v; docker compose up -d --wait db
uv run python scripts/load_data.py data/coffee_health_raw.csv    # resumen: 9.975 cargadas
uv run python scripts/load_data.py data/coffee_health_raw.csv    # 0 insertadas, 0 actualizadas
docker compose exec db psql -U postgres -d coffee -c "select count(*) from health_records"   # 9975
docker compose exec db psql -U postgres -d coffee -c "select reason, count(*) from load_rejects group by 1 order by 2 desc"   # suma 37
uv run pytest
uv run ruff check .; uv run ruff format --check .; uv run mypy
docker compose stop db; uv run pytest tests/test_validation.py; docker compose start db      # pasa sin BD
```

```powershell
git add -A; git commit -m "feat: CSV loader with validation, dedupe and idempotent upsert"
git switch main; git merge --no-ff 001-csv-loader
```

Recomiendo un commit por fase (spec, plan, tasks, implementación): el historial muestra el proceso.

### Paso 5 — Feature 002: API

```powershell
git switch -c 002-records-api
```

**5.1 Especificar**

```
/speckit-specify API de consulta de registros "coffee health". Quiero que un cliente pueda consultar por HTTP los registros ya cargados, filtrarlos y recorrerlos por páginas, usando la documentación interactiva como única interfaz.

Historias por prioridad:
P1. Listar registros paginados: nunca se devuelven todas las filas. El cliente pide un tamaño de página (por defecto 50, máximo 200) y recibe los registros en orden estable por ID junto con el cursor de la página siguiente, o nulo si no hay más. Recorrer todas las páginas devuelve cada registro exactamente una vez. Una página profunda responde tan rápido como la primera.
P1. Filtrar: selección múltiple (coincide con cualquiera de los valores dados) por country, gender, sleep_quality y stress_level; booleano por smoking; rangos inclusivos min/max para age, coffee_intake y bmi. Los filtros se combinan con AND. country no distingue mayúsculas ni espacios sobrantes; un país que no existe devuelve una página vacía, no un error.
P2. Errores claros: los parámetros inválidos devuelven 422 con un mensaje que nombra el parámetro: mínimo mayor que máximo (por ejemplo min_age > max_age), categoría desconocida, tamaño de página fuera de 1–200, cursor no numérico o negativo, y parámetros desconocidos (por ejemplo min_agee).
P3. Resumen (opcional): con los mismos filtros, devolver por grupo (country, gender, sleep_quality, stress_level o tazas de café en tramos de una taza) el número de registros y los promedios de sleep_hours, coffee_intake y bmi.
P3. Filas no cargadas (opcional): listado paginado de los rechazos de la carga (archivo, línea, motivo, columna, valor) con filtro por motivo y por archivo, y el conteo por motivo.

Además: los valores numéricos se devuelven como números JSON, no como cadenas. Si la base está saturada, el servicio responde un error temporal claro en lugar de quedarse colgado.
Escala: la tabla puede crecer a decenas de millones de filas y el diseño debe sostenerlo (paginación, índices, conexiones).
Fuera de alcance: escritura o edición, autenticación, ordenamientos arbitrarios, total de coincidencias en el listado (lo da el resumen), front end.
```

**5.2 Aclarar** — `/speckit-clarify`, con la tabla de API de la sección 2.

**5.3 Planear**

```
/speckit-plan Stack del repo: FastAPI >= 0.115, psycopg 3 con ConnectionPool, Pydantic v2. Sin ORM ni dependencias nuevas.

- app/schemas.py: modelos Pydantic de respuesta. Record; RecordPage {items, next_after_id, limit}; SummaryRow. StrEnum compartidos con app/validation.py. Decimales expuestos como float.
- app/filters.py: RecordFilters, modelo Pydantic usado como query params con Annotated[RecordFilters, Query()], extra="forbid" y un model_validator que exige min <= max. Función PURA build_where(filters) -> (sql.Composable, params) con psycopg.sql y parámetros; = ANY(%s) para multiselección. Nunca se interpola entrada del usuario; group_by se traduce con un dict fijo a identificadores.
- app/records.py: APIRouter con GET /records, GET /records/summary, GET /rejects (keyset por source_file y line_number, filtros reason y source_file) y GET /rejects/summary (conteo por motivo), incluido en app/main.py. Se conservan /health y /notes.
- Paginación keyset: WHERE id > after_id ... ORDER BY id LIMIT limit+1; la fila extra solo sirve para calcular next_after_id. Sin OFFSET y sin count(*).
- Índices en db/init/003_indexes.sql: la PK (id) cubre el listado sin filtros y los filtros poco selectivos (gender, stress_level, sleep_quality, smoking, rangos amplios), recorriendo la PK y filtrando. Añadir solo (country, id) para el filtro selectivo más probable. Justificar en research.md con EXPLAIN (ANALYZE, BUFFERS) de la primera página frente a una profunda y de keyset frente a OFFSET; documentar el costo de escritura de cada índice y el peor caso (combinaciones de filtros muy raras).
- Conexiones: endpoints def (threadpool) y pool acotado; tamaño de pool y timeouts configurables en Settings; statement_timeout por conexión; PoolTimeout se traduce a 503. Documentar: workers × max_size < max_connections, y las alternativas async y PgBouncer.
- Pruebas: tests/test_filters.py sin BD (validación de parámetros y build_where), escritas antes que la implementación. tests/test_records_api.py con TestClient contra la BD real: un fixture inserta y borra filas con IDs >= 9.000.000 y country 'Testland' para no depender de los datos cargados.
```

**5.4** `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`

**5.5 Verificar**

```powershell
docker compose down -v; docker compose up -d --wait db
uv run python scripts/load_data.py data/coffee_health_raw.csv
uv run pytest
uv run ruff check .; uv run ruff format --check .; uv run mypy
uv run fastapi dev app/main.py     # probar en http://localhost:8000/docs
```

Pruebas manuales en `/docs`: `country=Germany&country=Spain&limit=5`; seguir `next_after_id` dos páginas; `min_age=50&max_age=20` → 422; `limit=500` → 422; `/records/summary?group_by=country`.

```powershell
git add -A; git commit -m "feat: records API with filters, keyset pagination and summary"
git switch main; git merge --no-ff 002-records-api
```

### Paso 6 — NOTES.md y cierre

Pídelo en una sesión normal, sin ciclo SDD nuevo: «Escribe NOTES.md en inglés a partir de `specs/001-*/` y `specs/002-*/` (spec, research, data-model) y del resultado real de la carga». Contenido:

1. **Schema**: tipos, constraints, índices y por qué.
2. **Cleaning and validation**: tabla corregible / inválido, límites numéricos, y qué se rechazó: conteo por motivo de las 37 filas no cargadas (25 rechazos + 12 duplicados) y la consulta a `load_rejects` que las lista con línea y motivo.
3. **Duplicates**: política de última aparición.
4. **Filtering and pagination**: filtros elegidos, keyset vs offset.
5. **Scale**: memoria y tiempo del loader (con la medición), costo de índices, página profunda (con el EXPLAIN), conexiones.
6. **Lint exceptions**: cada `noqa` o `type: ignore` con su motivo (la prueba pide explicarlos), o "none".
7. **How AI was used**: SDD con spec-kit; los artefactos están en `specs/`.

La prueba pide un NOTES.md **corto** y que se entienda solo: `specs/` es material de apoyo, no lo sustituye. Qué pide documentar la prueba y dónde queda:

| Lo que piden | Sección de NOTES.md |
|---|---|
| Esquema | 1 |
| Cómo limpia y valida el loader, y qué rechazó | 2 |
| Límites numéricos elegidos | 2 |
| Casos ambiguos (mismo ID con valores distintos): qué elegiste y por qué | 3 |
| Estrategia de filtros y paginación | 4 |
| Decisiones de escala, compromisos y suposiciones | 5 |
| Excepciones de lint, si las hay | 6 |

Verificación final, tal como la hará quien revise (Git Bash con make, o los equivalentes):

```bash
make db-reset && make load && make test && make lint
make dev
```

```powershell
git add -A; git commit -m "docs: add NOTES.md"
git push -u origin main
```

Entrega el enlace del fork. No abras un pull request contra el repo de Medallo salvo que te lo pidan.

### Paso 7 (opcional) — Feature 003: interfaz React

Solo cuando el paso 6 esté completo y subido. La prueba dice que no hace falta front end: no suma en lo que evalúan y todo lo que entregues lo tienes que poder explicar. Hazlo si te sobra tiempo.

Condiciones para que no estorbe:

- Aislado en `frontend/`; `make test`, `make lint` y `make db-reset && make load && make dev` siguen igual.
- Servicio `web` en `docker-compose.yml` bajo el perfil `ui`: quien revise no necesita Node y `make db-up` no lo levanta.
- Pocas piezas: Vite + React + TypeScript, un archivo CSS propio y `fetch`. Sin router, sin librería de estado, sin kit de componentes, sin librería de gráficos.
- Único cambio en la API: CORS para `http://localhost:5173`.

```powershell
git switch -c 003-web-ui
```

```
/speckit-specify Interfaz web mínima para explorar los datos "coffee health". Una sola página con tres secciones: (1) Registros: panel de filtros (país, género, calidad de sueño, nivel de estrés, fumador, rangos de edad, café y BMI), tabla de resultados y botón "Cargar más" que pide la página siguiente; (2) Resumen: promedios por grupo como barras horizontales, con los mismos filtros; (3) Calidad de datos: conteo de filas no cargadas por motivo y su listado. Estados de carga, vacío y error visibles; los errores 422 de la API se muestran junto al filtro que los causó. Usable en escritorio y en móvil. Fuera de alcance: edición de datos, autenticación, ordenamiento por columna, exportar.
```

```
/speckit-plan Carpeta frontend/ con Vite + React + TypeScript. Sin router, sin librería de estado, sin kit de componentes ni de gráficos: fetch, useState y useEffect, un archivo CSS con variables y barras hechas con CSS. La URL de la API sale de VITE_API_URL (por defecto http://localhost:8000). En la API, CORSMiddleware con el origen permitido en Settings (por defecto http://localhost:5173). Docker: servicio web en docker-compose.yml con profiles: ["ui"], imagen node:22-alpine, la carpeta frontend montada como volumen, node_modules en un volumen anónimo (ningún volumen con nombre nuevo, para que docker compose down -v siga afectando solo a la base), frontend/node_modules y frontend/dist en .gitignore, comando "npm ci && npm run dev -- --host" y puerto 5173. No se toca el servicio db ni los targets existentes del Makefile; se puede añadir un target make ui. Verificación: tsc --noEmit y vite build sin errores, y prueba manual de las tres secciones.
```

`/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`

```powershell
docker compose --profile ui up -d web      # la API sigue en el host: uv run fastapi dev app/main.py
# abrir http://localhost:5173
uv run pytest; uv run ruff check .; uv run ruff format --check .; uv run mypy    # lo obligatorio sigue en verde

# Con la UI levantada, repetir el flujo obligatorio completo: debe terminar sin errores
docker compose down -v; docker compose up -d --wait db
uv run python scripts/load_data.py data/coffee_health_raw.csv
uv run pytest

git add -A; git commit -m "feat: optional React UI"
git switch main; git merge --no-ff 003-web-ui; git push
```

Añade a NOTES.md una sección corta "Optional web UI" con cómo levantarla.

---

## 4. Pruebas propuestas

### 4.1 Validación y normalización — sin BD (`tests/test_validation.py`)

Parametrizadas con `pytest.mark.parametrize`.

**Estructura de fila**
- 16 campos válidos → registro válido, `fixed=False`
- 13 campos y 17 campos → rechazo `column_count`
- Fila con todos los campos entre comillas → válida y no cuenta como corregida
- Línea en blanco → se omite, no es rechazo
- Cabecera con BOM → reconocida; cabecera con columna faltante o reordenada → error fatal

**Enteros (ID, Age, Heart_Rate)**
- `'40'` → 40; `' 40 '` → 40 y `fixed=True`
- Rechazo: `'4O'`, `'thirty'`, `''`, `'unknown'`, `'-3'`, `'+40'`, `'40.0'`, `'4_0'`, `'1e2'`, dígitos no ASCII (`'４０'`)
- Límites de Age: 17 ✗, 18 ✓, 120 ✓, 121 ✗, 150 ✗, 212 ✗
- Límites de Heart_Rate: 0 ✗, 29 ✗, 30 ✓, 200 ✓, 201 ✗, 350 ✗
- ID: `'0'` ✗, mayor que 2.147.483.647 ✗

**Decimales (Coffee_Intake, Caffeine_mg, Sleep_Hours, BMI, Physical_Activity_Hours)**
- `'3.5'` → `Decimal('3.5')`; `'3'` → válido; el resultado es `Decimal`, no `float`
- Rechazo: `''`, `'n/a'`, `'seven'`, `'3,5'`, `'.5'`, `'5.'`, `'-1'`, `'NaN'`, `'Infinity'`, `'1e3'`
- `'3.14'` → `3.1` y `fixed=True`; `'3.15'` → `3.2`
- Límites por columna: sueño 0.9 ✗, 1.0 ✓, 16.0 ✓, 16.1 ✗, 25.0 ✗; BMI 0 ✗, 9.9 ✗, 10.0 ✓, 80.0 ✓; café 0 ✓, 20.0 ✓, 20.1 ✗; cafeína 2000.0 ✓, 2000.1 ✗; actividad 80.0 ✓, 80.1 ✗

**Categóricos**
- Valor exacto → igual, `fixed=False`
- `'  Female  '`, `'\tMale  '`, `'MALE'`, `'male'`, `'GOOD'`, `' Good '`, `'low'` → canónico y `fixed=True`
- Rechazo: `''`, `'?'`, `'N/A'`, `'Unknown'`
- `Health_Issues='None'` es un valor válido (la cadena `None`, no un nulo)
- Cada valor de cada catálogo se acepta (un test por catálogo)

**País**
- `'USA'` → `'USA'`; `' usa '` → `'USA'`; `'uk'` → `'UK'` (no `'Usa'` ni `'Uk'`)
- `'south korea'`, `'  South Korea  '` → `'South Korea'`
- `'SWEDEN'`, `'belgium'`, `'\tBrazil  '` → canónico
- `'Atlantis'` → rechazo `unknown_country`

**Booleanos**
- `'0'` → False y `'1'` → True, `fixed=False`
- `'Y'`, `'y'`, `'yes'`, `'YES'`, `'true'` → True; `'N'`, `'no'`, `'false'` → False; todos `fixed=True`
- Rechazo: `'-1'`, `'2'`, `''`, `'maybe'`, y lo que Pydantic aceptaría por defecto: `'on'`, `'off'`, `'t'`, `'f'`

**Fila completa**
- Fila con dos columnas inválidas → un solo rechazo que nombra las dos
- Fila corregida y además inválida → cuenta solo como rechazada
- El rechazo conserva el número de línea y el valor original sin recortar
- Normalizar un registro ya normalizado devuelve lo mismo con `fixed=False`
- `parse_row` no modifica la lista de entrada

**Prueba de caracterización (sin BD, lee el CSV real)**
- Recorrer `data/coffee_health_raw.csv` con el validador da 10.012 filas, 25 rechazos con el desglose por motivo, 9.987 válidas y 56 corregidas

### 4.2 Loader — con BD (`tests/test_load_data.py`)

Cada prueba en una transacción con rollback, con CSV pequeños e IDs ≥ 9.000.000.

- El resumen coincide con las filas de la tabla
- Ejecutar dos veces: la segunda da 0 insertadas, 0 actualizadas y el mismo `count(*)`
- Conflicto por ID: quedan los valores de la última línea; la anterior aparece en `load_rejects` como `duplicate_conflict` con la línea que la reemplazó
- Duplicado exacto → `duplicate_exact`
- Recargar con un valor cambiado → 1 actualizada, 0 insertadas
- Una fila inválida a mitad del archivo no detiene la carga
- Los números de línea en `load_rejects` son correctos después de una línea en blanco y de una fila entre comillas
- Ejecutar dos veces no duplica rechazos: mismo `count(*)` en `load_rejects`
- Cargar un segundo archivo no borra los rechazos del primero
- Un bloque pequeño (`chunk_size=3`) da el mismo resultado que un solo bloque, también con duplicados repartidos entre bloques
- Invariante: leídas = rechazadas + duplicadas + cargadas
- La BD rechaza por CHECK un insert directo fuera de rango
- Archivo con BOM y CRLF se carga igual que uno sin BOM y con LF

### 4.3 Filtros y consulta — sin BD (`tests/test_filters.py`)

- Sin filtros → sin condiciones y sin parámetros
- Un valor y varios valores → `= ANY(...)` con la lista en parámetros
- Solo mínimo, solo máximo, ambos; `min == max` es válido
- `min > max` en age, coffee_intake y bmi → error que nombra ambos parámetros
- Categoría desconocida → error
- `limit` 0 ✗, 1 ✓, 200 ✓, 201 ✗; `after_id` negativo ✗
- Parámetro desconocido → error
- `country=' germany '` → `'Germany'`; un país desconocido pasa recortado
- Un valor malicioso (`"'; drop table health_records;--"`) solo aparece en los parámetros, nunca en el texto SQL
- `group_by` fuera de la lista permitida → error

### 4.4 API — con BD (`tests/test_records_api.py`, `TestClient`)

Un fixture inserta 5 o 6 filas con `country='Testland'` e IDs ≥ 9.000.000 y las borra al terminar.

- `GET /records` → 200, como máximo 50 elementos, claves esperadas, numéricos como números JSON
- Recorrido completo con `limit=2`: sin repetidos, sin huecos, el último `next_after_id` es nulo
- Cada filtro por separado y varios combinados con AND
- Los rangos son inclusivos en ambos extremos
- Multiselección: `gender=Male&gender=Other`
- País inexistente → 200 con lista vacía
- 422: `min_age=50&max_age=20`, `limit=0`, `limit=201`, `gender=X`, `after_id=abc`, `min_agee=3`; el cuerpo nombra el parámetro
- `/records/summary?group_by=gender&country=Testland` → conteos y promedios exactos
- El resumen respeta los filtros
- `GET /rejects?source_file=test.csv&reason=…` devuelve solo ese motivo y pagina sin repetidos; `GET /rejects/summary` cuenta por motivo (filas de prueba con `source_file='test.csv'`)
- `/health` y `/notes` siguen pasando
- Si el dataset está cargado: la suma de conteos del resumen es igual a `select count(*)` (se omite con `skip` si la tabla está vacía)

---

## 5. Evidencia de escala que conviene medir

Para NOTES.md, mejor un número medido que una estimación:

| Pregunta de la prueba | Qué medir |
|---|---|
| ¿Archivo que no cabe en memoria? ¿Cuánto tarda? | Generar un CSV de 1–5 millones de filas replicando el original con IDs desplazados; medir filas/s y memoria pico del loader |
| ¿Qué índices y cuánto cuestan? | Tiempo de carga con y sin `(country, id)`; tamaño de cada índice con `pg_relation_size` |
| ¿Página 1 vs página 50.000? | `EXPLAIN (ANALYZE, BUFFERS)` de keyset frente a `OFFSET` sobre la tabla grande |
| ¿Conexiones bajo carga? | Explicar pool (máximo 10) frente a threadpool (40 hilos), timeout → 503, workers × pool < `max_connections` |

Borra la tabla y el CSV sintéticos al terminar (`docker compose down -v` y recargar).

## 6. Preguntas que debes poder responder sin la IA

- ¿Qué arquitectura usaste y por qué no una hexagonal o con repositorios?
- ¿Por qué rechazas `4O` en vez de corregirlo? ¿Y por qué sí corriges `Y`?
- ¿Por qué gana la última fila duplicada? ¿Qué pasaría con "gana la primera"?
- ¿Cómo garantizas que ejecutar dos veces no duplica? ¿Qué hace `ON CONFLICT ... WHERE IS DISTINCT FROM`?
- ¿Por qué una tabla de staging y no un `set` de IDs en Python?
- ¿Por qué keyset y no `OFFSET`? ¿Qué pierdes (saltar a la página N, total)?
- ¿Por qué no hay índice en `gender`? ¿Cuándo añadirías uno?
- ¿Por qué `numeric` y no `float`? ¿Por qué `text + CHECK` y no `ENUM`?
- ¿Por qué los rechazos van a una tabla? ¿Cómo evitas que se dupliquen al recargar?
- ¿Por qué no usaste un ORM? ¿Dónde viven las reglas de validación?
- ¿Por qué no dejas que Pydantic convierta los números y booleanos por su cuenta?
- ¿Qué pasa si llegan 100 peticiones simultáneas con un pool de 10?
