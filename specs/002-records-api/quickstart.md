# Quickstart: validar la API de punta a punta

**Feature**: 002-records-api | Contrato: [contracts/api.md](contracts/api.md) |
Modelo: [data-model.md](data-model.md)

## Requisitos

- Docker en marcha, `uv` y `make` (o los equivalentes de PowerShell de `PLAN-SDD.md`).
- `.env` creado con `make setup`. En este equipo `DATABASE_URL` usa `127.0.0.1` (research R12 de
  la feature 001).

## 1. Base limpia, carga y servicio

```powershell
make db-reset      # aplica también db/init/003_indexes.sql
make load          # 9984 registros, 28 filas no cargadas
make dev           # http://localhost:8000/docs
```

`make db-reset` es obligatorio la primera vez: los scripts de `db/init/` solo se ejecutan al
crear el volumen.

## 2. Pruebas y calidad

```powershell
make test
make lint
docker compose stop db
uv run pytest tests/test_validation.py tests/test_filters.py    # pasan sin base de datos
docker compose start db
```

## 3. Comprobaciones en `/docs`

Con el archivo actual cargado. Cada fila es una petición y lo que debe devolver.

| Petición | Esperado |
|---|---|
| `GET /records` | 50 registros desde el ID 1; `next_after_id` 50 o mayor; `limit` 50 |
| `GET /records?limit=200`, siguiendo `next_after_id` hasta `null` | 50 páginas, 9.984 registros |
| `GET /records?after_id=10000` | `items` vacío, `next_after_id` `null` |
| `GET /records?country=Germany&limit=200`, siguiendo el cursor | 497 registros |
| `GET /records?country=germany&country=%20SPAIN%20&limit=200`, siguiendo el cursor | 983 registros |
| `GET /records?country=Atlantis` | `items` vacío, sin error |
| `GET /records?gender=female&country=Germany&limit=200`, siguiendo el cursor | 249 registros |
| `GET /records?min_age=50&max_age=20` | 422, nombra `max_age` y `min_age` |
| `GET /records?limit=500` | 422, nombra `limit` |
| `GET /records?min_agee=30` | 422, nombra `min_agee` |
| `GET /records/summary?group_by=gender` | 4 grupos: 4.989, 4.767, 225 y 3 (clave `null`); suman 9.984 |
| `GET /records/summary?group_by=coffee_cups` | 9 grupos, claves 0 a 8 |
| `GET /records/summary?group_by=gender&country=Germany` | Los conteos suman 497 |
| `GET /rejects` | 28 filas ordenadas por línea |
| `GET /rejects?reason=not_numeric` | 5 filas; la línea 816 con `Age` y `thirty` |
| `GET /rejects/summary` | 8, 6, 5, 4, 3 y 2 (ver contrato) |
| `GET /health`, `GET /notes` | Como antes |

En cualquier respuesta, los valores numéricos (`coffee_intake`, `bmi`, promedios) aparecen sin
comillas.

## 4. Escala (SC-003)

```powershell
uv run python scripts/make_big_csv.py data/coffee_health_raw.csv 100 $env:TEMP\big.csv
uv run python scripts/load_data.py $env:TEMP\big.csv      # ~1 M de filas
make dev
```

Medir el tiempo de respuesta de `GET /records?limit=50` (primera página) y de
`GET /records?limit=50&after_id=990009900` (última), y lo mismo con `country=Germany`. Las
cuatro deben tardar menos de 200 ms y la profunda como máximo el doble que la primera. Anotar
los resultados y el tiempo de la carga en research.md R15 y en NOTES.md. Después, borrar el
archivo grande y volver a la base limpia (`make db-reset`, `make load`).

## 5. Error temporal (FR-030)

Lo cubren los tests de integración con un pool de una conexión. A mano: con el servicio en
marcha, `docker compose stop db`, pedir `GET /records` y comprobar que responde 503 en 6
segundos como mucho en vez de quedarse esperando; después `docker compose start db`.
