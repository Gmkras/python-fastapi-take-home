# Contract: CLI del loader

**Feature**: 001-csv-loader

## Invocación

```text
uv run python scripts/load_data.py <csv_path> [--chunk-size N]
make load        # = uv run python scripts/load_data.py data/coffee_health_raw.csv
```

| Argumento | Tipo | Defecto | Regla |
|---|---|---|---|
| `csv_path` | ruta | — | Obligatorio; archivo legible |
| `--chunk-size` | entero | 50000 | ≥ 1 |

Entorno: `DATABASE_URL` (vía `.env` y `app.config.settings`).

## Salida estándar (éxito)

Formato de texto estable, una métrica por línea, para leerlo a simple vista y en los tests:

```text
Loaded data/coffee_health_raw.csv
  rows read            10012
  blank lines              3
  valid                 9996
    fixed                 56
    with missing values    9
  rejected                16
    out_of_range           6
    not_numeric            5
    column_count           3
    invalid_boolean        2
  duplicates              12
    duplicate_exact        8
    duplicate_conflict     4
  inserted              9984
  updated                  0
  unchanged                0
  rows in table         9984
  rejects in table        28
Elapsed <s> s (<rows/s> rows/s)
```

El desglose de rechazos muestra solo los motivos con conteo mayor que cero, ordenados de mayor
a menor. Los números son los esperados en base vacía con el archivo actual. Desglose por
columna de esos 16: Age 5 (3 `not_numeric`: `thirty`, `4O`, `-3`; 2 `out_of_range`: 150, 212),
Sleep_Hours 3 (2 `not_numeric`: `seven`, `-1`; 1 `out_of_range`: 25.0), Heart_Rate 2
`out_of_range` (0, 350), BMI 1 `out_of_range` (0), Smoking 2 `invalid_boolean` (`-1`, `2`) y 3
`column_count`.

## Errores (stderr)

| Caso | Mensaje (inglés) | Código |
|---|---|---|
| Archivo inexistente o ilegible | `error: cannot read <path>: <motivo>` | 1 |
| Cabecera distinta | `error: unexpected header` + esperado y encontrado | 1 |
| Sin conexión o timeout | `error: cannot connect to database: <motivo>` | 1 |
| Invariante incumplido | `error: reconciliation failed: <qué no cuadra>`; no se confirma nada | 1 |
| Argumento inválido | mensaje de argparse | 2 |

En todos los casos con código distinto de cero, la base queda como estaba.

## Consulta de filas no cargadas (contrato de datos)

La tabla `load_rejects` ([data-model.md](../data-model.md) §2) es la interfaz para explicar
las filas no cargadas:

```sql
-- conteo por motivo de la última carga del archivo
SELECT reason, count(*) FROM load_rejects
WHERE source_file = 'coffee_health_raw.csv' GROUP BY reason ORDER BY count(*) DESC;

-- una fila concreta
SELECT * FROM load_rejects
WHERE source_file = 'coffee_health_raw.csv' AND line_number = 816;
```

La feature 002 expondrá esta misma tabla por HTTP.
