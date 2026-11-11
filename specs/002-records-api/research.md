# Research: API de consulta de registros Coffee Health

**Feature**: 002-records-api | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

Formato de cada decisión: Decision / Rationale / Alternatives considered.

**Cómo se midió**. Las cifras de planes de consulta salen del Postgres 17 del proyecto, en una
tabla temporal con la misma estructura y los mismos `CHECK` que `health_records`, cargada con
100 copias de los datos reales en orden de ID (998.400 filas, 116 MB; 1.198.080 filas en las
mediciones posteriores a crear el índice). La tabla temporal desaparece al cerrar la sesión: la
base real no se tocó. Dos salvedades de medir así: los buffers aparecen como `local` en vez de
`shared`, y Postgres no usa consultas en paralelo sobre tablas temporales, así que los tiempos
de los recorridos completos (R9) son pesimistas. Los comportamientos de FastAPI y del pool se
comprobaron con prototipos (FastAPI 0.162.2, Pydantic 2.14.3, psycopg 3.3.8, psycopg-pool 3.3.3).

## R1. Paginación por cursor sobre el ID (keyset)

**Decision**: `WHERE id > :after_id ... ORDER BY id LIMIT :limit + 1`. Sin `OFFSET` y sin
`count(*)`.

**Rationale**: el costo de una página no depende de su posición, porque el índice de la PK
salta directamente al cursor. Medido sin filtros:

| Consulta | Filas leídas | Buffers | Tiempo |
|---|---|---|---|
| Keyset, primera página | 51 | 4 | 0,08 ms |
| Keyset, página profunda (últimas 5.000 filas) | 51 | 5 | 0,06 ms |
| `OFFSET 993400`, la misma página | 993.451 | 17.427 | 122 ms |

`OFFSET` lee y descarta todas las filas anteriores: unas 2.000 veces más lento a 1 millón de
filas, y crece de forma lineal. Además, keyset es estable si se insertan filas durante el
recorrido (no repite ni salta registros); `OFFSET` no.

**Alternatives considered**: `OFFSET/LIMIT` con número de página (lo anterior; su única ventaja
es saltar a una página arbitraria, que la spec no pide); cursores de servidor (atan una
conexión a cada cliente).

## R2. Forma de la página y cálculo del cursor

**Decision**: respuesta `{items, next_after_id, limit}`. Se piden `limit + 1` filas: si llega la
fila extra, hay más páginas, se descarta y `next_after_id` es el ID del último elemento
devuelto; si no llega, `next_after_id` es `null`. `after_id` es un entero ≥ 0 con valor por
defecto 0 (los IDs empiezan en 1).

**Rationale**: la fila extra responde "¿hay más?" sin una segunda consulta ni un `count(*)`, y
hace que el cursor sea nulo también cuando la última página queda exactamente llena (FR-006).
El cursor es el ID tal cual: transparente y fácil de usar desde la documentación interactiva.

**Alternatives considered**: devolver siempre el último ID como cursor (obliga a una petición
vacía final); cursor opaco codificado (innecesario con una clave de una sola columna); incluir
el total (cuesta recorrer todas las coincidencias en cada página; lo da el resumen).

## R3. Parámetros como un modelo Pydantic

**Decision**: un modelo Pydantic por endpoint, usado como `Annotated[Modelo, Query()]`, con
`extra="forbid"`.

**Rationale**: las reglas quedan declaradas en un lugar, se prueban sin HTTP ni base de datos, y
FastAPI devuelve el 422 y documenta cada parámetro sin código propio. Comprobado en el prototipo:

| Entrada | Respuesta 422 (`type`, `loc`) |
|---|---|
| `min_agee=30` | `extra_forbidden`, `["query", "min_agee"]` |
| `limit=0`, `limit=201`, `limit=abc` | `greater_than_equal`, `less_than_equal`, `int_parsing`, `["query", "limit"]` |
| `after_id=-1` | `greater_than_equal`, `["query", "after_id"]` |
| `gender=Robot` | `enum`, `["query", "gender", 0]`, mensaje con los valores admitidos |
| `min_age=30.5`, `min_bmi=abc`, `smoking=maybe` | `int_parsing`, `decimal_parsing`, `bool_parsing` |
| Varios inválidos a la vez | Un error por parámetro, todos en la misma respuesta |

Los parámetros repetidos (`country=Germany&country=Spain`) llegan como lista. En los rangos
decimales, `NaN` e `Infinity` se rechazan sin código propio (error `finite_number`). La documentación
interactiva muestra cada parámetro con su tipo, límites, valor por defecto y lista de valores.

**Alternatives considered**: parámetros sueltos en la firma de cada endpoint (un parámetro
desconocido se ignora en silencio, justo lo que FR-017 prohíbe, y los mismos 13 filtros habría
que repetirlos en dos endpoints).

## R4. Validación de mínimo ≤ máximo, por campo

**Decision**: un `field_validator` sobre cada `max_*` que lee el `min_*` ya validado
(`info.data`). El texto del `/speckit-plan` proponía un `model_validator`.

**Rationale**: en el prototipo, un `model_validator(mode="after")` no se ejecuta si algún campo
ya falló: con `min_age=50&max_age=20&limit=0` la respuesta solo nombraba `limit`, lo que
incumple FR-018 (nombrar todos los parámetros inválidos). Además su error llega con
`loc = ["query"]`, sin parámetro. El validador de campo sí se acumula con los demás errores y
apunta al parámetro:

```text
min_age=50&max_age=20&limit=0&min_bmi=30&max_bmi=25&x=1  →  422 con cuatro errores:
  limit · max_age ("max_age must be greater than or equal to min_age") · max_bmi · x
```

Si el mínimo es inválido, solo se informa ese error (no hay con qué comparar). Mínimo igual a
máximo es válido. Requiere declarar cada `min_*` antes que su `max_*`.

**Alternatives considered**: `model_validator` (lo descrito); validar en el endpoint (deja de ser
lógica pura y sale de la documentación).

## R5. Normalización de los filtros de selección

**Decision**: un validador previo recorta y compara sin distinguir mayúsculas (`casefold`), y
entrega el valor canónico:

- `gender`, `sleep_quality`, `stress_level` (y `reason` en las filas no cargadas): se traducen
  al miembro del `StrEnum` de `app/validation.py`. Lo que no esté en el catálogo falla con el
  error `enum` estándar, que lista los valores admitidos.
- `country`: se traduce con el catálogo `COUNTRIES` de la carga (`usa` → `USA`). Un valor que
  no está en el catálogo se deja recortado tal cual: no coincide con nada y devuelve una página
  vacía (FR-012).

**Rationale**: clarificación Q3. El tipo del campo sigue siendo `list[Gender]`, así que la
documentación conserva la lista cerrada (comprobado: el esquema OpenAPI referencia el enum con
`Male`, `Female`, `Other`) y `female` o ` MALE ` se aceptan. Es la misma regla que aplica la
carga, con los mismos catálogos: una sola fuente.

Como la base guarda siempre la forma canónica, la consulta compara por igualdad
(`country = ANY(...)`) y puede usar un índice normal.

**Alternatives considered**: `lower(country) = ANY(...)` con un índice de expresión sobre
`lower(country)`. No depende del catálogo, pero añade una expresión que hay que repetir igual
en la consulta y en el índice. Sería la opción si algún día entraran países sin pasar por la
carga. Parámetros `str` libres para las categorías: se pierde la lista en la documentación.

## R6. Construcción del WHERE como función pura

**Decision**: `build_where(filters) -> (sql.Composable, list[params])` en `app/filters.py`, con
`psycopg.sql`. Cada filtro presente aporta una condición con marcador `%s`; la selección
múltiple usa `columna = ANY(%s)` con una lista. Los nombres de columna salen de constantes del
código; `group_by` se traduce con un diccionario fijo. Nunca se interpola un valor del cliente.

**Rationale**: constitución II (lógica pura con tests sin base). Comprobado: `as_string()` de
psycopg 3.3 funciona sin conexión, así que el test compara el SQL generado y sus parámetros sin
Postgres. `= ANY(%s)` admite cualquier número de valores con un solo marcador. Comprobado
también que una lista de miembros `StrEnum` se envía como `text[]` sin conversión.

**Alternatives considered**: `IN (%s, %s, ...)` (hay que generar marcadores); construir el SQL
con f-strings (riesgo de inyección).

## R7. Índices: la PK y `(country, id)`

**Decision**: `db/init/003_indexes.sql` crea un solo índice nuevo:
`CREATE INDEX ... ON health_records (country, id)`.

**Rationale**: medido con y sin el índice.

| Consulta (1 M de filas) | Solo PK | Con `(country, id)` |
|---|---|---|
| `country = Germany`, primera página | 0,21 ms (lee 1.001 filas, descarta 950) | 0,19 ms (mismo plan) |
| `country = Germany`, página profunda | 0,24 ms | 0,28 ms (mismo plan) |
| `country = Germany, Spain`, página profunda | — | 0,14 ms (por la PK) |
| `country = Atlantis` (no existe) | **110 ms** (recorre toda la tabla) | **0,09 ms** |
| Dos países que no existen | — | 0,08 ms |

Lo que muestran los números:

1. Para un país frecuente (cada uno es un 5 % de los datos) basta la PK: se recorre desde el
   cursor y se descartan unas 20 filas por cada coincidencia. Es igual de rápido en cualquier
   página, y el planificador sigue eligiendo la PK aunque exista el otro índice.
2. El índice `(country, id)` se justifica por los países con pocas filas o inexistentes. Sin él,
   un nombre mal escrito obliga a recorrer toda la tabla: 110 ms con 1 millón de filas, alrededor
   de 1 s con 10 millones, y más de 5 s (el límite, es decir un 503 en vez de una página vacía)
   hacia los 50 millones. Con él, el costo es constante.
3. También sirve al resumen filtrado por país (R9).

Los filtros poco selectivos (`gender`, `sleep_quality`, `stress_level`, `smoking`, rangos
amplios) no necesitan índice: `gender = Female` en página profunda tarda 0,11 ms por la PK.

**Costo de escritura** (medido): insertar 99.840 filas tarda 405 ms solo con la PK y 565 ms con
los dos índices: +39 % en ese paso, unos 1,6 µs por fila. Para la carga completa (50 s por
millón de filas, casi todo validación) es alrededor de un 3 %; se vuelve a medir al implementar.
Construirlo sobre 1,1 M de filas tarda 2,5 s y ocupa 35 MB (la PK ocupa 26 MB; la tabla, 139 MB).

**Alternatives considered**: sin índice adicional (deja el caso 2 sin resolver); un índice por
filtro (clarificación Q2: descartado por su costo en la carga, y los filtros de baja
selectividad no lo aprovechan); índice solo sobre `(country)` (no devuelve las filas en orden de
ID, obliga a ordenar).

## R8. Sin sentencias preparadas en el servidor

**Decision**: las conexiones del pool se crean con `prepare_threshold=None`.

**Rationale**: por defecto psycopg prepara en el servidor una consulta tras ejecutarla 5 veces
en la misma conexión, y Postgres puede entonces pasar a un plan genérico, que no conoce los
valores. Medido forzando el plan genérico: `country = Atlantis` vuelve a recorrer toda la tabla
por la PK (180 ms) e ignora el índice de R7, porque el plan no sabe que el país es raro. Con
`prepare_threshold=None` cada consulta se planifica con sus valores reales; planificar cuesta
entre 0,1 y 0,7 ms.

**Alternatives considered**: `plan_cache_mode = force_custom_plan` en el servidor (mismo efecto,
pero es configuración de Postgres fuera del repo); dejar el valor por defecto (rendimiento
impredecible según el historial de cada conexión).

## R9. Peor caso y resumen: lo que crece con la tabla

**Decision**: se acepta y se documenta que estas consultas cuestan en proporción a las filas
que recorren, acotadas por el límite de tiempo (R11):

| Consulta (1 M de filas, solo PK) | Filas recorridas | Tiempo |
|---|---|---|
| Combinación poco frecuente: Spain + fumador + BMI 25–30 (0,26 %) | 19.724 | 4,8 ms |
| Filtro sin coincidencias que no es un país (`min_age=119`) | toda la tabla | 85 ms |
| Resumen por género sin filtros (1,2 M de filas) | toda la tabla | 426 ms |
| Resumen por tazas de café filtrado por país | 59.640 (por el índice) | 54 ms |

**Rationale**: clarificación Q2 (la rapidez en profundidad se garantiza sin filtros y por país).
Un filtro sin índice con muy pocas coincidencias recorre la PK hasta reunir la página; si no hay
ninguna, recorre todo: unos 0,85 s con 10 millones de filas y el límite de 5 s hacia los 60
millones. El resumen sin filtros recorre siempre toda la tabla: en esta medición (sin
paralelismo, por ser tabla temporal) alcanzaría los 5 s hacia los 14 millones de filas.

Evolución si la tabla llega a ese tamaño, no construida ahora (constitución IV): para el
resumen, una tabla de agregados por grupo actualizada al final de cada carga; para filtros
concretos que se demuestren frecuentes y selectivos, un índice `(columna, id)` como el de país.

**Alternatives considered**: índices para todas las combinaciones (imposible de mantener);
límite de tiempo más alto solo para el resumen (configurable; no hace falta con los datos de
hoy).

## R10. Números como números JSON

**Decision**: los modelos de respuesta declaran `float` para las columnas `numeric`.

**Rationale**: Pydantic serializa `Decimal` como cadena y la spec pide números (FR-020).
Comprobado: `Decimal("24.9")` → `24.9` y `Decimal("3.0")` → `3.0` en el JSON. Con un solo
decimal no hay pérdida visible. Los filtros de rango sí usan `Decimal`, para comparar de forma
exacta con las columnas `numeric`.

**Alternatives considered**: convertir en SQL (`::float8`) (misma salida, más ruido en cada
consulta); dejar cadenas (incumple FR-020).

## R11. Conexiones, límites de tiempo y error temporal

**Decision**:
- Endpoints `def` (FastAPI los ejecuta en su grupo de hilos) con el `ConnectionPool` síncrono
  que ya trae el repo.
- Pool acotado y configurable en `Settings`: `db_pool_min_size` (1), `db_pool_max_size` (10),
  `db_pool_timeout_seconds` (5: espera máxima por una conexión) y `db_statement_timeout_ms`
  (5000: duración máxima de una consulta).
- El límite de consulta se fija al abrir cada conexión del pool
  (`options="-c statement_timeout=..."`), no en `DATABASE_URL`: la carga usa su propia conexión
  y no debe heredarlo.
- Tres errores se traducen a **503**: `PoolTimeout` (base saturada o caída), `QueryCanceled`
  (consulta que superó el límite) y `OperationalError` (conexión rota).
- El pool comprueba cada conexión antes de entregarla
  (`check=ConnectionPool.check_connection`) y la reemplaza si el servidor la había cerrado.

**Rationale**: clarificación Q5 y FR-030. Comprobado en el prototipo con un pool de una
conexión: con la conexión ocupada, la petición devuelve 503 al vencer el tiempo de espera
(0,5 s configurados → 506 ms); con `statement_timeout=300`, `pg_sleep(2)` devuelve 503 a los
310 ms, y la siguiente petición funciona con normalidad (el pool recupera la conexión).

La comprobación al entregar se añadió después de implementar, al verificar a mano: tras
reiniciar la base con el servicio en marcha, la primera petición devolvió 503 aunque la base ya
estaba disponible, porque el pool entregó una conexión que el servidor había cerrado. Se
reprodujo cerrando la conexión desde el servidor (`pg_terminate_backend`), que es lo que le hace
un reinicio:

| Pool | Tres peticiones seguidas tras cerrarse la conexión | Tiempo por petición |
|---|---|---|
| Sin comprobación | falla, bien, bien | 1,51 ms |
| Con comprobación al entregar | bien, bien, bien | 1,98 ms |

Cuesta un viaje más a la base por petición (unos 0,5 ms en este equipo) y evita un 503 que no
corresponde: en un despliegue real las conexiones se cierran por reinicios, conmutaciones o
inactividad. Las cifras de R15 son anteriores a este cambio; hay que sumarles ese medio
milisegundo. Si la base está realmente caída, el comportamiento no cambia: 503.

Regla de dimensionamiento: `procesos × db_pool_max_size < max_connections` de Postgres (100 por
defecto). El grupo de hilos de FastAPI admite 40 peticiones a la vez; las que no consiguen
conexión esperan como mucho `db_pool_timeout_seconds`.

**Alternatives considered**: endpoints `async` con pool asíncrono (más peticiones simultáneas
por proceso, pero cambia `db.py` y todos los endpoints, y el límite real sigue siendo el número
de conexiones a Postgres); PgBouncer delante de Postgres (lo indicado con muchos procesos o
réplicas; es infraestructura fuera del alcance de la prueba); sin límites (una consulta lenta o
una base caída dejarían las peticiones colgadas); sin comprobación al entregar la conexión (un
viaje menos por petición, a cambio de un 503 por cada conexión cerrada tras un reinicio).

## R12. Resumen por grupo

**Decision**: una consulta `GROUP BY` sobre la expresión de agrupación, con los mismos filtros
que el listado:

```sql
SELECT <expr> AS key, count(*), round(avg(sleep_hours), 2), round(avg(coffee_intake), 2),
       round(avg(bmi), 2)
FROM health_records WHERE <filtros> GROUP BY 1 ORDER BY 1 NULLS LAST
```

`<expr>` sale de un diccionario fijo: la columna para `country`, `gender`, `sleep_quality` y
`stress_level`; `floor(coffee_intake)::int` para `coffee_cups`.

**Rationale**: clarificación Q1. `GROUP BY` ya forma un grupo con clave `NULL` para los
registros sin el dato, así que los conteos suman el total; `avg` ignora los `NULL` y devuelve
`NULL` si no hay ningún valor. No hace falta código para nada de esto. `floor` da tramos de una
taza: el grupo `n` es `[n, n + 1)`. Se devuelve todo en una respuesta: como mucho 20 grupos por
país y 9 por tazas con los datos actuales.

**Alternatives considered**: agregar en Python (trae todas las filas al proceso); `width_bucket`
(más general de lo necesario para tramos de una unidad).

## R13. Listado de filas no cargadas

**Decision**: keyset sobre la PK compuesta de `load_rejects`:
`WHERE (source_file, line_number) > (:f, :n) ORDER BY source_file, line_number LIMIT :limit + 1`.
El cursor son dos parámetros, `after_source_file` y `after_line_number`, que van juntos; la
respuesta trae `next_after` con esos dos valores, o `null`. Filtros: `reason` (uno o varios) y
`source_file` (nombre exacto). El conteo es `GROUP BY reason`.

Las condiciones las construye una función pura, `build_reject_where`, igual que `build_where`
(R6): las dos rutas la usan y se prueba sin base de datos.

Si llega solo uno de los dos parámetros del cursor, el error (`cursor_incomplete`) se sitúa
siempre en `after_line_number` y su mensaje nombra los dos. Es la técnica de R4: un validador
de campo solo puede leer los campos declarados antes que él, así que valida el segundo, con
`validate_default=True` para que se ejecute también cuando falta. Comprobado en prototipo:
se informa junto a otros errores de la misma petición.

**Rationale**: la comparación de filas usa el índice de la PK (comprobado: `Index Cond:
ROW(source_file, line_number) > ROW(...)`), así que cumple las mismas reglas que el listado de
registros sin índices nuevos.

**Alternatives considered**: cursor de un solo valor codificado como `archivo:línea` (hay que
codificar y validar un formato propio); paginar solo dentro de un archivo (el orden entre
archivos quedaría sin definir).

## R14. Estrategia de pruebas

**Decision**:
- `tests/test_filters.py`, sin base de datos y escrito antes que la implementación: cada regla
  de validación (R3, R4, R5) y el SQL y los parámetros que produce `build_where` (R6).
- `tests/test_records_api.py`, con `TestClient` contra Postgres: un fixture inserta y confirma
  filas con IDs ≥ 9.000.000 y país `Testland`, y las borra al terminar.

**Rationale**: los endpoints usan las conexiones del pool, así que no ven datos sin confirmar:
el aislamiento por `rollback` de la feature 001 no sirve aquí. Con IDs por encima de los reales
y un país que no existe en los datos, cada test selecciona solo sus filas (`country=Testland` o
`after_id=8999999`) y no depende de lo que haya cargado. `Testland` no está en el catálogo, así
que la tolerancia a mayúsculas del país se prueba en `test_filters.py`, que es donde vive.

El fixture borra solo las claves que insertó, nunca por rango: así no puede tocar datos
ajenos. Los tests suponen que no hay filas reales con ID ≥ 9.000.000; por eso no se ejecutan
con el archivo de 1 millón de filas cargado (R15).

El 503 se prueba sin simular nada, sobre la ruta real: la saturación, con un pool de una
conexión y tiempo de espera corto (como en el prototipo); la consulta demasiado lenta, tomando
desde otra conexión un bloqueo exclusivo sobre `health_records` dentro de una transacción que
no se confirma, con el límite de consulta reducido a 200 ms. La espera por el bloqueo cuenta
para `statement_timeout`, así que la petición se cancela; el bloqueo dura lo que dura el test.

**Alternatives considered**: probar contra los datos cargados (los tests fallarían en una base
vacía o con otro archivo); mocks de la base (no detectan errores de SQL).

## R15. Lo que se mide al implementar

**Decision**: SC-003 (página profunda ≤ el doble de la primera y ambas < 200 ms, de extremo a
extremo por HTTP) se mide con la tabla real de 1 millón de filas, generada con
`scripts/make_big_csv.py`, y el resultado se anota aquí y en NOTES.md. En la misma medición se
vuelve a tomar el tiempo de la carga con el índice nuevo (R7).

**Rationale**: los planes de esta investigación miden la consulta; SC-003 mide la respuesta
completa, con serialización incluida.

**Resultado** (2026-11-11). Equipo: AMD Ryzen 7 3700X (8 núcleos, 16 hilos), 32 GB de RAM,
Windows 11 Pro, Postgres 17 en Docker Desktop. Tabla real con 998.400 registros (100 copias del
archivo). Tiempo de extremo a extremo con `TestClient` (validación, consulta y serialización, sin
red), mediana de 20 repeticiones tras una de calentamiento:

| Petición | Mediana | Máximo |
|---|---|---|
| Primera página, sin filtros (`limit=50`) | 6,0 ms | 6,9 ms |
| Página profunda, sin filtros | 7,0 ms | 8,2 ms |
| Primera página, `country=Germany` | 7,2 ms | 8,9 ms |
| Página profunda, `country=Germany` | 6,7 ms | 7,6 ms |
| Primera página, `limit=200` | 8,5 ms | 10,2 ms |
| Página profunda, `limit=200` | 8,7 ms | 13,8 ms |
| `country=Atlantis` (no existe) | 7,4 ms | 10,8 ms |
| `gender=Female`, página profunda | 7,7 ms | 11,1 ms |
| Combinación poco frecuente (Spain, fumador, BMI 25–30) | 12,3 ms | 14,6 ms |
| Sin coincidencias en un filtro sin índice (`min_age=119`) | 47,5 ms | 53,4 ms |
| Resumen por género, sin filtros | 182,9 ms | 220,9 ms |
| Resumen por tazas de café, `country=Germany` | 52,5 ms | 57,5 ms |

- **SC-003 se cumple**: la página profunda tarda 1,17 veces la primera sin filtros y 0,93 veces
  filtrando por país; las cuatro están por debajo de 10 ms, frente al límite de 200 ms. Casi
  todo el tiempo es de la aplicación (validar y serializar), no de la consulta, que R1 midió
  en 0,06 ms.
- Los dos recorridos completos son más rápidos que en R9, porque sobre la tabla real Postgres
  sí trabaja en paralelo: el filtro sin coincidencias tarda 47,5 ms (85 ms en R9) y el resumen
  sin filtros 183 ms (426 ms en R9). Con esta cifra, el resumen sin filtros llegaría al límite
  de 5 s hacia los 27 millones de filas, no hacia los 14 de R9.
- **Carga con el índice nuevo**: 52,8 s para 1.001.200 filas (18.956 filas/s). Las mediciones
  sin el índice dieron entre 49,6 y 53,4 s (research R15 de la feature 001), así que en la carga
  completa el costo del índice no se distingue de la variación entre ejecuciones. El +39 % de R7
  es solo el paso de inserción, que es una parte pequeña del total. El ritmo sigue en el límite
  de los 20.000 filas/s de SC-006 de la feature 001, como antes.
- **Error temporal**: con el servicio en marcha y la base detenida, `GET /records` respondió 503
  de inmediato, y volvió a responder 200 al arrancar la base, sin reiniciar el servicio.

## Resumen de NEEDS CLARIFICATION

Ninguno: el stack venía fijado por el repo y la constitución, y las decisiones de comportamiento
se cerraron en `/speckit-clarify`.
