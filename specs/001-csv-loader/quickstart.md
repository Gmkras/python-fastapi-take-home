# Quickstart: validar el loader de punta a punta

**Feature**: 001-csv-loader | Contratos: [contracts/cli.md](contracts/cli.md) |
Modelo: [data-model.md](data-model.md)

## Requisitos

- Docker en marcha, `uv` y `make` (o los equivalentes de PowerShell de `PLAN-SDD.md`).
- `.env` creado con `make setup`. En este equipo, `DATABASE_URL` usa `127.0.0.1` en vez de
  `localhost` (ver research R12).

## 1. Base limpia y primera carga

```powershell
make db-reset
make load
```

Esperado (SC-001):
- Código de salida 0.
- `rows read 10012`, `blank lines 3`, `rejected 16`, `duplicates 12`, `fixed 56`,
  `with missing values 9`, `inserted 9984`, `rows in table 9984`, `rejects in table 28`.

## 2. Comprobar en la base

```powershell
make db-shell
```

```sql
SELECT count(*) FROM health_records;                         -- 9984
SELECT count(*) FROM health_records
 WHERE age IS NULL OR gender IS NULL OR sleep_hours IS NULL
    OR bmi IS NULL OR heart_rate IS NULL;                     -- 9
SELECT reason, count(*) FROM load_rejects GROUP BY 1;        -- suma 28
SELECT line_number, column_name, raw_value, reason
  FROM load_rejects WHERE line_number IN (816, 2659, 3339);
-- 816: Age / thirty / not_numeric
-- 2659: column_count
-- 3339: duplicate_conflict, detail "superseded by line 3516"
SELECT age, heart_rate FROM health_records WHERE id = 3338;  -- 26, 86 (línea 3516)
SELECT DISTINCT country FROM health_records ORDER BY 1;      -- 20 países canónicos
```

## 3. Idempotencia (SC-004)

```powershell
make load
```

Esperado: `inserted 0`, `updated 0`, `unchanged 9984`, `rows in table 9984`,
`rejects in table 28`.

## 4. Pruebas y calidad

```powershell
make test
make lint
docker compose stop db
uv run pytest tests/test_validation.py      # pasa sin base de datos
docker compose start db
```

## 5. Escala (SC-006)

```powershell
uv run python scripts/make_big_csv.py data/coffee_health_raw.csv 100 $env:TEMP\big.csv   # ~1 M filas
uv run python scripts/load_data.py $env:TEMP\big.csv
```

Anotar tiempo, filas/s y memoria pico en research.md R15 y en NOTES.md. Después, borrar el
archivo grande y volver a la base limpia (`make db-reset`, `make load`).
