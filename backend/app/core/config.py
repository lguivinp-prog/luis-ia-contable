from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    timezone: str = "America/Lima"
    log_level: str = "INFO"

    database_url: str
    test_database_url: str | None = None

    session_secret: str
    session_cookie_name: str = "luis_ia_contable_session"
    session_ttl_hours: int = 12
    session_cookie_secure: bool = False

    ruc_lookup_provider: Literal["fake", "sunat"] = "fake"
    ruc_lookup_api_token: str | None = None

    backend_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
