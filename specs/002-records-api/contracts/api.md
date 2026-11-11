# Contract: API HTTP de consulta

**Feature**: 002-records-api | Modelos en [data-model.md](../data-model.md)

Solo lectura. Todas las rutas son `GET` y responden JSON. La documentación interactiva
(`/docs`) se genera de estos mismos modelos y es la interfaz prevista.

## Rutas

| Ruta | Devuelve | Historia |
|---|---|---|
| `GET /records` | Una página de registros | US1, US2 |
| `GET /records/summary` | Conteo y promedios por grupo | US4 |
| `GET /rejects` | Una página de filas no cargadas | US5 |
| `GET /rejects/summary` | Conteo de filas no cargadas por motivo | US5 |
| `GET /health`, `GET /notes` | Existentes, sin cambios | FR-034 |

## `GET /records`

| Parámetro | Tipo | Defecto | Regla |
|---|---|---|---|
| `limit` | entero | 50 | 1–200 |
| `after_id` | entero | 0 | ≥ 0; el `next_after_id` de la página anterior |
| `country` | texto, repetible | — | Sin distinguir mayúsculas ni espacios |
| `gender` | `Male`, `Female`, `Other`; repetible | — | Sin distinguir mayúsculas ni espacios |
| `sleep_quality` | `Poor`, `Fair`, `Good`, `Excellent`; repetible | — | Igual |
| `stress_level` | `Low`, `Medium`, `High`; repetible | — | Igual |
| `smoking` | booleano | — | `true`/`false`; también `1`/`0`, `yes`/`no`, `on`/`off`, sin distinguir mayúsculas |
| `min_age`, `max_age` | entero | — | Inclusivos; `max_age ≥ min_age` |
| `min_coffee_intake`, `max_coffee_intake` | decimal | — | Inclusivos; `max ≥ min` |
| `min_bmi`, `max_bmi` | decimal | — | Inclusivos; `max ≥ min` |

Un parámetro repetido coincide con cualquiera de sus valores; parámetros distintos se combinan
con Y. Un filtro sobre una columna no incluye los registros sin ese dato.

```text
GET /records?country=Germany&country=spain&smoking=true&min_age=30&limit=2
```

```json
{
  "items": [
    {"id": 17, "age": 34, "gender": "Male", "country": "Germany", "coffee_intake": 3.5,
     "caffeine_mg": 328.1, "sleep_hours": 7.5, "sleep_quality": "Good", "bmi": 24.9,
     "heart_rate": 78, "stress_level": "Low", "physical_activity_hours": 14.5,
     "health_issues": "None", "occupation": "Other", "smoking": true,
     "alcohol_consumption": false},
    {"id": 52, "age": 41, "gender": null, "country": "Spain", "...": "..."}
  ],
  "next_after_id": 52,
  "limit": 2
}
```

Los valores del ejemplo son ilustrativos. `next_after_id` es `null` cuando no hay más
registros. Para seguir: la misma petición con `after_id=52`.

## `GET /records/summary`

| Parámetro | Tipo | Regla |
|---|---|---|
| `group_by` | `country`, `gender`, `sleep_quality`, `stress_level`, `coffee_cups` | Obligatorio |
| Filtros | Los mismos que `/records`, sin `limit` ni `after_id` | — |

```text
GET /records/summary?group_by=gender
```

```json
[
  {"key": "Female", "count": 4989, "avg_sleep_hours": 6.64, "avg_coffee_intake": 2.5, "avg_bmi": 23.95},
  {"key": "Male", "count": 4767, "avg_sleep_hours": 6.64, "avg_coffee_intake": 2.52, "avg_bmi": 24.02},
  {"key": "Other", "count": 225, "avg_sleep_hours": 6.6, "avg_coffee_intake": 2.48, "avg_bmi": 23.88},
  {"key": null, "count": 3, "avg_sleep_hours": 5.93, "avg_coffee_intake": 2.13, "avg_bmi": 27.27}
]
```

Estos son los valores reales con el archivo actual. Los grupos van ordenados por clave, con el
grupo "sin dato" (`null`) al final. Con `group_by=coffee_cups` la clave es un entero `n` que
reúne los registros con `n ≤ coffee_intake < n + 1`. Un promedio sin ningún dato es `null`. Sin
coincidencias, la respuesta es `[]`.

## `GET /rejects`

| Parámetro | Tipo | Defecto | Regla |
|---|---|---|---|
| `limit` | entero | 50 | 1–200 |
| `after_source_file`, `after_line_number` | texto, entero ≥ 0 | — | Los dos o ninguno; el `next_after` de la página anterior |
| `reason` | motivo, repetible | — | Uno de los nueve códigos de la feature 001 |
| `source_file` | texto | — | Nombre exacto del archivo |

```text
GET /rejects?reason=not_numeric&limit=1
```

```json
{
  "items": [
    {"source_file": "coffee_health_raw.csv", "line_number": 816, "reason": "not_numeric",
     "column_name": "Age", "raw_value": "thirty", "detail": "Age='thirty': not_numeric"}
  ],
  "next_after": {"source_file": "coffee_health_raw.csv", "line_number": 816},
  "limit": 1
}
```

Orden: `source_file`, `line_number`. Para seguir:
`after_source_file=coffee_health_raw.csv&after_line_number=816`.

## `GET /rejects/summary`

| Parámetro | Tipo | Regla |
|---|---|---|
| `source_file` | texto | Opcional; nombre exacto |

```json
[
  {"reason": "duplicate_exact", "count": 8},
  {"reason": "out_of_range", "count": 6},
  {"reason": "not_numeric", "count": 5},
  {"reason": "duplicate_conflict", "count": 4},
  {"reason": "column_count", "count": 3},
  {"reason": "invalid_boolean", "count": 2}
]
```

Orden: de mayor a menor conteo y, a igualdad, por motivo.

## Errores

| Código | Cuándo | Cuerpo |
|---|---|---|
| 422 | Parámetro inválido o desconocido | El estándar de FastAPI: `{"detail": [{"type", "loc", "msg", "input"}, ...]}`, un elemento por parámetro; `loc` es `["query", "<parámetro>"]` |
| 503 | Base saturada o caída, o consulta que supera el límite de tiempo | `{"detail": "..."}` con un mensaje que indica que es temporal |

Casos que producen 422:

| Petición | Parámetro nombrado |
|---|---|
| `min_age=50&max_age=20` | `max_age` (el mensaje nombra también `min_age`) |
| `gender=Robot` | `gender`, con los valores admitidos |
| `limit=0`, `limit=201`, `limit=abc` | `limit` |
| `after_id=-1`, `after_id=abc` | `after_id` |
| `min_agee=30` | `min_agee` |
| `min_bmi=abc`, `min_age=30.5`, `smoking=maybe` | El parámetro correspondiente |
| `group_by=planet` o sin `group_by` en el resumen | `group_by` |
| `after_line_number=5` sin `after_source_file` (o al revés) | `after_line_number`, siempre; el mensaje nombra los dos |
| `reason=typo` | `reason` |

No son errores (200 con resultado vacío): un país que no existe, un archivo que no existe, un
cursor más allá del final, un rango sin coincidencias.
