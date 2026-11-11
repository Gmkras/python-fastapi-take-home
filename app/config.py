from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5433/coffee"

    # Connection pool of the API. The loader opens its own connection and ignores these.
    # Keep (worker processes x db_pool_max_size) below Postgres' max_connections.
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_pool_timeout_seconds: float = 5.0  # longest wait for a free connection, then 503
    db_statement_timeout_ms: int = 5000  # longest a query may run, then 503


settings = Settings()
