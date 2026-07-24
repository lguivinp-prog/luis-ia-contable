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

    # ---- Bloqueo de login por intentos fallidos (ADR-0009) ----
    login_max_attempts: int = 5
    login_attempt_window_minutes: int = 15
    login_lockout_duration_minutes: int = 15
    login_lockout_max_minutes: int = 60

    # ---- Seguridad de sesiones ----
    session_inactivity_timeout_minutes: int = 30
    session_inactivity_warning_seconds: int = 60
    session_max_concurrent: int = 5

    # ---- Motor de cronogramas: limites de importacion CSV ----
    tax_schedule_csv_max_bytes: int = 2_000_000
    tax_schedule_csv_max_rows: int = 5000

    # ---- Ciclo operativo (Bloque 3.2): cierre con impuesto/documentos pendientes ----
    # Interruptor global deliberadamente en False por defecto (ver "Cierre
    # operativo completo" del plan / supuesto 1): el cierre normal ya
    # funciona sin este flag; habilitarlo solo permite que, ADEMAS, un
    # admin pueda forzar un cierre excepcional con `force_close_with_pending`
    # + motivo explicito por cada llamada (ver ADR-0047/Dominio D).
    allow_close_with_pending_tax: bool = False

    # ---- Reportes (Bloque 3.2) ----
    report_export_max_rows: int = 20000

    # ---- Almacenamiento de archivos documentales (Bloque 5, ADR-0053) ----
    # Ruta deliberadamente fuera de /app (el bind mount de codigo fuente) --
    # respaldada por un volumen Docker nombrado, nunca un bind mount del host.
    document_storage_root: str = "/var/lib/luis-ia-contable/documents"
    document_file_max_bytes: int = 15_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
