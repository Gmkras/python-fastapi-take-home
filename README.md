# Medallo Take Home (Python)

A Python project with a [FastAPI](https://fastapi.tiangolo.com/) API and a [PostgreSQL](https://www.postgresql.org/) database running in Docker.

[Español abajo](#configuración-español)

---

## Setup (English)

### System requirements

| Requirement | Version | Install |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | 0.5 or later | [docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation/) |
| [Docker](https://www.docker.com/) | Latest stable | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| `make` | Any | Preinstalled on macOS and Linux. On Windows, use WSL or run the commands in the table below directly. |

uv installs the correct Python version (3.12 or later, see `.python-version`) for you. You do not need to install Python separately.

Docker must be running before you start the database.

```bash
uv --version
docker --version
```

### Installation

1. Clone the repository and enter the project directory:

   ```bash
   git clone <repository-url>
   cd python-fastapi-take-home
   ```

2. Install dependencies and create your `.env` file:

   ```bash
   make setup
   ```

3. Start Postgres in Docker:

   ```bash
   make db-up
   ```

4. Start the API:

   ```bash
   make dev
   ```

   Open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive API docs. `GET /health` and `GET /notes` should both work.

5. Check that everything is connected:

   ```bash
   make test
   ```

### Database

Postgres runs in the `db` service defined in `docker-compose.yml`. Both the API and the loader script connect to it using `DATABASE_URL` from `.env`:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/coffee
```

The host port is **5433** so it does not conflict with a Postgres you may already have on 5432. You can connect with any client (psql, TablePlus, DBeaver, etc.) using those credentials, or run `make db-shell`.

SQL files in `db/init/` run in filename order **only when the database volume is first created**. The sample `db/init/001_notes.sql` creates a `notes` table. After you add or change files there, run `make db-reset` to delete the data and apply them again. If you prefer a migration tool (Alembic, yoyo, dbmate, etc.), you may use one instead.

### Commands

| Command | What it runs | Purpose |
| --- | --- | --- |
| `make setup` | `uv sync` and copy `.env.example` → `.env` | Install dependencies |
| `make db-up` | `docker compose up -d --wait db` | Start Postgres |
| `make db-down` | `docker compose down` | Stop Postgres (data is kept) |
| `make db-reset` | `docker compose down -v` then `up` | Delete all data and re-run `db/init/*.sql` |
| `make db-shell` | `docker compose exec db psql -U postgres -d coffee` | Open psql |
| `make dev` | `uv run fastapi dev app/main.py` | Run the API with auto-reload on port 8000 |
| `make load` | `uv run python scripts/load_data.py data/coffee_health_raw.csv` | Run the data loader |
| `make test` | `uv run pytest` | Run tests (the database must be running) |
| `make lint` | `ruff check`, `ruff format --check`, `mypy` | Lint, check formatting and type-check |

### Project layout

```
app/                 FastAPI application (main.py, db.py, config.py)
scripts/load_data.py Data loader (you implement this)
tests/               pytest tests
db/init/             SQL run when the database is first created
data/                The dataset
docker-compose.yml   Postgres
```

---

## Exercise (English)

Build a Python loader that imports the coffee health dataset into Postgres, and a FastAPI service that lets clients filter it.

### Dataset

`data/coffee_health_raw.csv` contains about **10,000 records** of synthetic health and coffee consumption data. Treat it as an export from another system: **it is not clean.** Some rows have formatting problems that can be fixed, some are invalid, and some records appear more than once.

Each record should have these columns:

| Column | Type | Expected values |
| --- | --- | --- |
| `ID` | integer | Unique identifier for a person |
| `Age` | integer | Adults |
| `Gender` | text | `Male`, `Female`, `Other` |
| `Country` | text | Country name, e.g. `Germany`, `South Korea` |
| `Coffee_Intake` | decimal | Cups per day |
| `Caffeine_mg` | decimal | Milligrams per day |
| `Sleep_Hours` | decimal | Hours per night |
| `Sleep_Quality` | text | `Poor`, `Fair`, `Good`, `Excellent` |
| `BMI` | decimal | Body mass index |
| `Heart_Rate` | integer | Resting beats per minute |
| `Stress_Level` | text | `Low`, `Medium`, `High` |
| `Physical_Activity_Hours` | decimal | Hours per week |
| `Health_Issues` | text | `None`, `Mild`, `Moderate`, `Severe` |
| `Occupation` | text | `Office`, `Healthcare`, `Service`, `Student`, `Other` |
| `Smoking` | boolean | 0 or 1 |
| `Alcohol_Consumption` | boolean | 0 or 1 |

Numeric values should be physically plausible. You decide the exact limits, and document them.

### Part 1 — Load the data with Python

1. **Design the schema.** Add your table(s) as SQL in `db/init/` (or use a migration tool). Choose types, constraints and indexes deliberately.
2. **Implement `scripts/load_data.py`.** It must:
   - Be written in Python. Using psycopg's `COPY ... FROM STDIN` from Python is fine; running `psql \copy` or a server-side `COPY` from a shell script instead of the loader is not.
   - Parse, validate and normalize each record before it reaches the database.
   - Keep going when a row is invalid. Report each rejected row with its line number and the reason, either on the console, in a file or in a table.
   - Be safe to run more than once. Running it twice must not duplicate data.
   - Print a summary at the end: rows read, loaded, fixed, rejected and duplicated (or whatever categories fit your approach).
3. **Run it:**

   ```bash
   make db-reset   # apply your schema to a fresh database
   make load
   ```

4. **Verify** that the number of rows in the database matches your loader's summary, and be ready to explain every row that was not loaded.

You decide how to handle ambiguous cases (for example, two records with the same `ID` but different values). Document what you chose and why.

### Part 2 — Serve the data with FastAPI

Add endpoint(s) to `app/main.py` (or new modules) that return the loaded records.

- **Filtering:** support a few meaningful filters, such as country, gender, sleep quality or stress level (exact match or multi-select), and numeric ranges for age, coffee intake or BMI. You decide which filters are most useful and how they behave.
- **Pagination is required.** Do not return all rows in one response.
- Use **Pydantic models** for responses, and return clear `422` errors for invalid parameters (for example `min_age` greater than `max_age`).
- **Optional:** a summary endpoint, e.g. average sleep hours grouped by coffee intake or country, with the same filters.

You do not need to build a front end. The automatically generated docs at `/docs` are the UI.

### Part 3 — Tests and code quality

- Write `pytest` tests for your validation and normalization logic. These should run without a database.
- Write at least one API test that uses `TestClient` against the real database.
- Use type hints. `make lint` runs ruff and mypy in strict mode. Aim to have it pass, or explain any exceptions.

### Part 4 — Think about scale

10,000 rows is small for Postgres, but large enough to expose naive patterns. **Design with scale in mind**: assume the CSV and the table could grow to tens of millions of rows. Consider, for example:

- Can your loader process a file that does not fit in memory? How long would it take?
- Which indexes do your filters need, and what do they cost when writing?
- How does your pagination behave on page 1 vs. page 50,000?
- How does the API use database connections under concurrent load?

Write down your trade-offs and assumptions. We are interested in how you reason about performance, not in one specific library choice.

### Using AI

AI-assisted development is welcome. You may use coding assistants, chatbots, or other AI tools while working on this exercise.

During review, we will assume you understand your submission and can explain your approach — why you made certain choices, how the solution works, and what trade-offs you considered. Use AI however helps you, but make sure you could walk us through the code without it.

### What to deliver

- A working local setup: `make db-reset && make load && make dev` loads the data and serves the API at [http://localhost:8000/docs](http://localhost:8000/docs).
- Tests that pass with `make test`.
- A short `NOTES.md` covering your schema, how the loader cleans and validates data (and what it rejected), your filtering and pagination strategy, and your scale decisions.

---

## Configuración (Español)

### Requisitos del sistema

| Requisito | Versión | Instalación |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | 0.5 o superior | [docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation/) |
| [Docker](https://www.docker.com/) | Última versión estable | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| `make` | Cualquiera | Viene instalado en macOS y Linux. En Windows, usa WSL o ejecuta directamente los comandos de la tabla de abajo. |

uv instala la versión correcta de Python (3.12 o superior, ver `.python-version`). No necesitas instalar Python por separado.

Docker debe estar en ejecución antes de iniciar la base de datos.

```bash
uv --version
docker --version
```

### Instalación

1. Clona el repositorio y entra al directorio del proyecto:

   ```bash
   git clone <url-del-repositorio>
   cd python-fastapi-take-home
   ```

2. Instala las dependencias y crea tu archivo `.env`:

   ```bash
   make setup
   ```

3. Inicia Postgres en Docker:

   ```bash
   make db-up
   ```

4. Inicia la API:

   ```bash
   make dev
   ```

   Abre [http://localhost:8000/docs](http://localhost:8000/docs) para ver la documentación interactiva de la API. `GET /health` y `GET /notes` deberían funcionar.

5. Comprueba que todo está conectado:

   ```bash
   make test
   ```

### Base de datos

Postgres se ejecuta en el servicio `db` definido en `docker-compose.yml`. Tanto la API como el script de carga se conectan usando `DATABASE_URL` de `.env`:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/coffee
```

El puerto del host es **5433** para no chocar con un Postgres que ya tengas en el 5432. Puedes conectarte con cualquier cliente (psql, TablePlus, DBeaver, etc.) usando esas credenciales, o ejecutar `make db-shell`.

Los archivos SQL de `db/init/` se ejecutan en orden de nombre **solo cuando se crea el volumen de la base de datos por primera vez**. El ejemplo `db/init/001_notes.sql` crea una tabla `notes`. Después de agregar o cambiar archivos ahí, ejecuta `make db-reset` para borrar los datos y aplicarlos de nuevo. Si prefieres una herramienta de migraciones (Alembic, yoyo, dbmate, etc.), puedes usarla.

### Comandos

| Comando | Qué ejecuta | Para qué |
| --- | --- | --- |
| `make setup` | `uv sync` y copia `.env.example` → `.env` | Instalar dependencias |
| `make db-up` | `docker compose up -d --wait db` | Iniciar Postgres |
| `make db-down` | `docker compose down` | Detener Postgres (los datos se conservan) |
| `make db-reset` | `docker compose down -v` y luego `up` | Borrar todos los datos y volver a ejecutar `db/init/*.sql` |
| `make db-shell` | `docker compose exec db psql -U postgres -d coffee` | Abrir psql |
| `make dev` | `uv run fastapi dev app/main.py` | Ejecutar la API con recarga automática en el puerto 8000 |
| `make load` | `uv run python scripts/load_data.py data/coffee_health_raw.csv` | Ejecutar el script de carga |
| `make test` | `uv run pytest` | Ejecutar las pruebas (la base de datos debe estar en ejecución) |
| `make lint` | `ruff check`, `ruff format --check`, `mypy` | Lint, formato y verificación de tipos |

### Estructura del proyecto

```
app/                 Aplicación FastAPI (main.py, db.py, config.py)
scripts/load_data.py Script de carga (lo implementas tú)
tests/               Pruebas con pytest
db/init/             SQL que se ejecuta al crear la base de datos
data/                El conjunto de datos
docker-compose.yml   Postgres
```

---

## Ejercicio (Español)

Construye un script de carga en Python que importe el conjunto de datos de café y salud a Postgres, y un servicio FastAPI que permita a los clientes filtrarlo.

### Conjunto de datos

`data/coffee_health_raw.csv` contiene unos **10,000 registros** sintéticos de salud y consumo de café. Trátalo como una exportación de otro sistema: **no está limpio.** Algunas filas tienen problemas de formato que se pueden corregir, otras son inválidas, y algunos registros aparecen más de una vez.

Cada registro debería tener estas columnas:

| Columna | Tipo | Valores esperados |
| --- | --- | --- |
| `ID` | entero | Identificador único de una persona |
| `Age` | entero | Adultos |
| `Gender` | texto | `Male`, `Female`, `Other` |
| `Country` | texto | Nombre del país, p. ej. `Germany`, `South Korea` |
| `Coffee_Intake` | decimal | Tazas por día |
| `Caffeine_mg` | decimal | Miligramos por día |
| `Sleep_Hours` | decimal | Horas por noche |
| `Sleep_Quality` | texto | `Poor`, `Fair`, `Good`, `Excellent` |
| `BMI` | decimal | Índice de masa corporal |
| `Heart_Rate` | entero | Pulsaciones por minuto en reposo |
| `Stress_Level` | texto | `Low`, `Medium`, `High` |
| `Physical_Activity_Hours` | decimal | Horas por semana |
| `Health_Issues` | texto | `None`, `Mild`, `Moderate`, `Severe` |
| `Occupation` | texto | `Office`, `Healthcare`, `Service`, `Student`, `Other` |
| `Smoking` | booleano | 0 o 1 |
| `Alcohol_Consumption` | booleano | 0 o 1 |

Los valores numéricos deben ser físicamente plausibles. Tú decides los límites exactos y los documentas.

### Parte 1 — Cargar los datos con Python

1. **Diseña el esquema.** Agrega tu(s) tabla(s) como SQL en `db/init/` (o usa una herramienta de migraciones). Elige tipos, restricciones e índices de forma deliberada.
2. **Implementa `scripts/load_data.py`.** Debe:
   - Estar escrito en Python. Usar `COPY ... FROM STDIN` de psycopg desde Python está bien; ejecutar `psql \copy` o un `COPY` del lado del servidor desde un script de shell en lugar del script de carga, no.
   - Analizar, validar y normalizar cada registro antes de que llegue a la base de datos.
   - Seguir adelante cuando una fila sea inválida. Reportar cada fila rechazada con su número de línea y el motivo, ya sea en la consola, en un archivo o en una tabla.
   - Poder ejecutarse más de una vez. Ejecutarlo dos veces no debe duplicar datos.
   - Imprimir un resumen al final: filas leídas, cargadas, corregidas, rechazadas y duplicadas (o las categorías que se ajusten a tu enfoque).
3. **Ejecútalo:**

   ```bash
   make db-reset   # aplica tu esquema a una base de datos nueva
   make load
   ```

4. **Verifica** que el número de filas en la base de datos coincide con el resumen de tu script, y prepárate para explicar cada fila que no se cargó.

Tú decides cómo manejar los casos ambiguos (por ejemplo, dos registros con el mismo `ID` pero valores distintos). Documenta qué elegiste y por qué.

### Parte 2 — Servir los datos con FastAPI

Agrega endpoint(s) en `app/main.py` (o en módulos nuevos) que devuelvan los registros cargados.

- **Filtros:** soporta algunos filtros útiles, como país, género, calidad del sueño o nivel de estrés (coincidencia exacta o selección múltiple), y rangos numéricos para edad, consumo de café o IMC. Tú decides qué filtros son más útiles y cómo se comportan.
- **La paginación es obligatoria.** No devuelvas todas las filas en una sola respuesta.
- Usa **modelos de Pydantic** para las respuestas y devuelve errores `422` claros para parámetros inválidos (por ejemplo, `min_age` mayor que `max_age`).
- **Opcional:** un endpoint de resumen, p. ej. promedio de horas de sueño agrupado por consumo de café o por país, con los mismos filtros.

No necesitas construir un front end. La documentación generada automáticamente en `/docs` sirve como interfaz.

### Parte 3 — Pruebas y calidad del código

- Escribe pruebas con `pytest` para tu lógica de validación y normalización. Deben ejecutarse sin base de datos.
- Escribe al menos una prueba de la API que use `TestClient` contra la base de datos real.
- Usa anotaciones de tipo. `make lint` ejecuta ruff y mypy en modo estricto. Intenta que pase, o explica las excepciones.

### Parte 4 — Piensa en la escala

10,000 filas es poco para Postgres, pero suficiente para revelar patrones ingenuos. **Diseña pensando en la escala**: supón que el CSV y la tabla podrían crecer a decenas de millones de filas. Considera, por ejemplo:

- ¿Tu script puede procesar un archivo que no cabe en memoria? ¿Cuánto tardaría?
- ¿Qué índices necesitan tus filtros y cuánto cuestan al escribir?
- ¿Cómo se comporta tu paginación en la página 1 frente a la página 50,000?
- ¿Cómo usa la API las conexiones a la base de datos con carga concurrente?

Documenta tus compromisos y suposiciones. Nos interesa cómo razonas sobre el rendimiento, no una librería en particular.

### Uso de IA

El desarrollo asistido por IA es bienvenido. Puedes usar asistentes de código, chatbots u otras herramientas de IA mientras trabajas en este ejercicio.

Durante la revisión, asumiremos que entiendes tu entrega y que puedes explicar tu enfoque — por qué tomaste ciertas decisiones, cómo funciona la solución y qué compromisos consideraste. Usa la IA como te ayude, pero asegúrate de que podrías repasar el código con nosotros sin depender de ella.

### Qué entregar

- Una configuración local funcional: `make db-reset && make load && make dev` carga los datos y sirve la API en [http://localhost:8000/docs](http://localhost:8000/docs).
- Pruebas que pasen con `make test`.
- Un `NOTES.md` breve que explique tu esquema, cómo el script limpia y valida los datos (y qué rechazó), tu estrategia de filtros y paginación, y tus decisiones de escala.
