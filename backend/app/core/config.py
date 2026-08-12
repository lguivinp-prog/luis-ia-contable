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

    # ---- Fase 3 / Bloque 3.1: integracion Google OAuth (ADR-0062/0063) ----
    # Vacios por defecto: sin estos dos valores la integracion queda
    # deliberadamente no configurada (INTEGRATION_MISCONFIGURED en el momento
    # de uso, nunca un fallo silencioso ni un valor de relleno).
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_provider: Literal["fake", "google"] = "fake"
    # Base publica usada para construir redirect_uri (ej. http://localhost:8000).
    # Nunca se envia al frontend como parte de un payload de configuracion.
    app_public_base_url: str = "http://localhost:8000"
    # A donde el callback redirige al navegador tras procesar la respuesta de
    # Google (el frontend corre en un origen distinto al backend).
    frontend_public_base_url: str = "http://localhost:3000"

    # ---- Cifrado de credenciales de integracion (ADR-0064) ----
    # Clave base64 de 32 bytes decodificados. Vacia por defecto: si Google
    # esta configurado pero esta clave falta o es invalida, los endpoints de
    # integracion fallan cerrado (INTEGRATION_MISCONFIGURED) -- ver
    # app/core/integration_crypto.py.
    integration_token_encryption_key: str = ""
    integration_token_key_id: str = "v1"

    # ---- Fase 5 / Bloque 5.1: boveda SOL (ADR-0068/0071) ----
    # `VaultUnlockRegistry` vive en memoria de un unico proceso -- 1 es el
    # unico valor aceptado (ver app/core/sol_vault_worker_gate.py). El
    # Dockerfile ya arranca uvicorn sin `--workers`, es decir con 1 proceso
    # real por defecto; este valor documenta y verifica esa realidad.
    backend_workers: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
