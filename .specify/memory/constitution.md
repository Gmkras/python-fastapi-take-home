<!--
Sync Impact Report
- Versión: (plantilla sin ratificar) → 1.0.0
- Principios: los 5 marcadores de la plantilla se reemplazan por 7 principios
  (I. Correctitud de datos primero … VII. Decisiones documentadas)
- Secciones añadidas: Restricciones técnicas y convenciones; Flujo de trabajo y puertas de calidad
- Secciones eliminadas: ninguna
- Plantillas dependientes: no se modifican (leen esta constitución al ejecutarse)
- TODO diferidos: ninguno
-->

# Coffee Health Constitution

## Core Principles

### I. Correctitud de datos primero

- Ningún registro llega a la base de datos sin haber sido parseado, validado y normalizado.
- Cada fila del archivo de origen que no se carga DEBE poder explicarse con su número de línea
  y su motivo, y esa explicación DEBE quedar consultable.
- Los conteos de cada carga DEBEN conciliar: leídas = cargadas + rechazadas + duplicadas. Si no
  concilian, la carga termina con error.
- La base de datos repite las reglas como restricciones (`NOT NULL`, `CHECK`) y es la última
  barrera, no la única.

Razón: el dataset es una exportación sucia; el valor de la prueba está en poder defender cada
fila que entró y cada fila que no.

### II. Lógica pura y testeable

- Validación, normalización y construcción de consultas son funciones puras: sin archivos, red
  ni base de datos.
- Esa lógica tiene pruebas pytest que DEBEN pasar con la base de datos apagada.
- Para esa lógica las pruebas se escriben primero (TDD): prueba que falla → implementación →
  refactor.
- La entrada/salida (lectura del CSV, HTTP, Postgres) vive en los bordes y se prueba con
  pruebas de integración contra la base real.

Razón: la prueba exige tests de validación sin base de datos; separar el núcleo puro lo
consigue sin mocks.

### III. Idempotencia y atomicidad

- Toda carga DEBE poder repetirse sin duplicar ni corromper datos, incluidos los rechazos
  registrados.
- Una carga que falla a mitad DEBE dejar la base exactamente como estaba (una transacción).

Razón: es un requisito explícito de la prueba y la condición para recargar con confianza.

### IV. Diseñado para escala, sin especular

- El loader DEBE usar memoria acotada, independiente del tamaño del archivo.
- El trabajo de volumen se hace por lotes y por conjuntos en Postgres, nunca fila a fila.
- La paginación DEBE costar lo mismo en la primera página que en una profunda.
- Cada índice se justifica con una consulta concreta y con su costo de escritura.
- Ninguna optimización sin un motivo medible.

Razón: la tabla puede crecer a decenas de millones de filas; los patrones ingenuos se notan
ya con 10.000.

### V. Simplicidad

- Stack fijo del repositorio: Python 3.12+, FastAPI, psycopg 3 con pool, Pydantic v2,
  Postgres 17, uv.
- SQL explícito y parametrizado; sin ORM.
- No se añaden dependencias sin una justificación escrita en el plan.
- El contrato del Makefile (`make db-reset`, `load`, `dev`, `test`, `lint`) no cambia.
- Sin capas ni abstracciones que el problema no pida (YAGNI).

Razón: todo lo entregado se debe poder explicar sin ayuda; cada pieza extra es algo más que
defender.

### VI. Calidad verificable

- Anotaciones de tipo en todo el código.
- `ruff check`, `ruff format --check` y `mypy` en modo estricto DEBEN pasar.
- Una tarea no está terminada hasta que las pruebas y el lint están en verde.
- Toda excepción de lint (`noqa`, `type: ignore`) lleva su motivo y se lista en NOTES.md.

Razón: `make test` y `make lint` son lo primero que ejecutará quien revise.

### VII. Decisiones documentadas

- Toda decisión ante un caso ambiguo (límites numéricos, duplicados, valores faltantes,
  formato de rechazos) queda escrita con su porqué en la spec de la feature.
- NOTES.md resume esas decisiones de forma breve y se entiende sin leer `specs/`.

Razón: la prueba evalúa el razonamiento, no solo el resultado.

## Restricciones técnicas y convenciones

- Código, identificadores, mensajes de commit y NOTES.md en inglés; specs, planes y tareas de
  spec-kit en español.
- El esquema se define como SQL en `db/init/` y se aplica con `make db-reset`.
- Configuración por `Settings` (pydantic-settings) desde `.env`; ningún secreto en el
  repositorio.
- Cualquier interfaz web es opcional, vive aislada en `frontend/` y no altera el flujo
  obligatorio `make db-reset && make load && make dev`.

## Flujo de trabajo y puertas de calidad

- Cada feature sigue el ciclo spec-kit: specify → clarify → plan → tasks → analyze →
  implement.
- Cada feature se desarrolla en su propia rama `NNN-nombre` y se integra en `main` solo con
  pruebas y lint en verde.
- Antes de integrar una feature se verifica en limpio: `make db-reset`, `make load`,
  `make test`, `make lint`.

## Governance

- Esta constitución prevalece sobre cualquier otra práctica del proyecto; los planes
  (`plan.md`) DEBEN incluir una comprobación contra ella.
- Toda desviación se justifica en la sección de complejidad del plan correspondiente.
- Enmiendas: se editan en este archivo con su motivo y se versionan con SemVer — MAJOR al
  eliminar o redefinir un principio, MINOR al añadir uno o ampliarlo de forma material, PATCH
  para aclaraciones de redacción.

**Version**: 1.0.0 | **Ratified**: 2026-11-11 | **Last Amended**: 2026-11-11
