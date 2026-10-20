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
    # M4 -- control SIRE: limites de archivos de respaldo/importacion (CSV, XLSX, PDF, imagenes)
    sire_file_max_bytes: int = 5_000_000
    sire_import_max_rows: int = 20_000

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

    # ---- Fase 5 / Bloque 5.2: acceso SOL supervisado, verificador de pairing
    # (ver docs/phase5/block5_2/, Domain A.3/C) ----
    # Clave servidor-side de proposito especifico para el verificador
    # HMAC-SHA-256 de pairing_code_verifier -- NUNCA reutiliza session_secret,
    # integration_token_encryption_key, ni ninguna clave del vault SOL (evita
    # domain confusion entre amenazas distintas). Vacia por defecto: si el
    # pairing se invoca sin esta clave configurada, falla cerrado
    # (PairingVerifierKeyMisconfiguredError en app/core/sol_pairing_crypto.py)
    # -- nunca un fallback inseguro ni un valor de relleno. Mismo formato ya
    # exigido por integration_token_encryption_key (ADR-0064, unico precedente
    # real de politica de clave en este repo): base64 estandar que decodifica
    # a exactamente 32 bytes (256 bits) de material aleatorio -- nunca un PIN,
    # palabra, ni "changeme".
    pairing_verifier_key: str = ""
    # `ONLINE`/`OFFLINE` de un agente nunca se persiste (Domain A.1 seccion 2)
    # -- se deriva de `last_seen_at` con este umbral. Valor deliberadamente
    # config (no hardcodeado): Domain A.1 lo dejo como decision diferida sin
    # impacto de DDL; Domain C lo fija con un default razonable, ajustable
    # sin migracion.
    sol_agent_online_threshold_seconds: int = 30

    # ---- Fase 5 / Bloque 5.2, Domain C.1 Gate 3: segunda capa de rate
    # limit sobre la CREACION de pairings (no sobre los intentos de codigo
    # dentro de un pairing -- eso ya lo cubre el lock durable de 5 fallos
    # en sol_agent_pairings). Mismo patron de conteo de filas +backoff
    # exponencial ya productivo de login_attempt_service.py (ADR-0009),
    # reutilizando `sol_agent_pairings.created_at` como bitacora -- sin
    # tabla nueva, sin Redis, sin infraestructura distribuida.
    pairing_creation_max_attempts: int = 5
    pairing_creation_lockout_duration_minutes: int = 15
    pairing_creation_lockout_max_minutes: int = 60

    # ---- Fase 5 / Bloque 5.2, Domain C.1a: segunda capa DISTINTA, sobre la
    # VERIFICACION de codigo de un pairing ya existente (no su creacion) --
    # ver app/core/sol_pairing_verification_throttle.py. Proceso-local
    # (nunca Redis/distribuido): ventana minima y acotada entre intentos
    # permitidos contra el MISMO (tenant_id, pairing_id). `0` desactiva el
    # throttle (usado por pruebas de concurrencia del quinto fallo).
    pairing_verification_throttle_seconds: float = 1.0

    # ---- Fase 5 / Bloque 5.2, Domain D: broker local (frontera de
    # confianza agente<->backend, ver docs/phase5/block5_2/) ----
    # SIEMPRE 127.0.0.1 en el codigo de arranque productivo (`server.py`
    # rechaza cualquier otro host al construir el listener -- ver
    # `BrokerServer.__init__`); este valor existe solo para que entornos de
    # prueba puedan variar el puerto, nunca el host.
    sol_broker_host: str = "127.0.0.1"
    # Puerto del listener de PAIRING (bootstrap, TLS servidor-only,
    # `CERT_NONE` -- el agente aun no tiene certificado propio). `0` deja que
    # el SO elija un puerto libre (usado por las pruebas).
    sol_broker_pairing_port: int = 8995
    # Puerto del listener OPERACIONAL (mTLS estricto, `CERT_REQUIRED`).
    # Deliberadamente distinto del de pairing -- nunca el mismo socket sirve
    # ambos modos de TLS (seccion 22 del encargo: "no falsear mTLS antes de
    # que exista un certificado").
    sol_broker_operational_port: int = 8996
    # Directorio de la CA local de agentes -- SIEMPRE fuera del repositorio y
    # fuera de PostgreSQL (mismo patron ya productivo de
    # `document_storage_root`, ADR-0053). Vacio de material -> arranque falla
    # cerrado (`app.broker.pki.CaMaterialMissingError`), nunca autogenera en
    # produccion.
    sol_broker_ca_dir: str = "/var/lib/luis-ia-contable/sol_broker_pki"
    sol_broker_tls_handshake_timeout_seconds: float = 10.0
    sol_broker_idle_timeout_seconds: float = 120.0
    sol_broker_read_timeout_seconds: float = 15.0
    # Cota de conexiones concurrentes aceptadas (pre y post autenticacion) --
    # evita que un numero no acotado de conexiones sin autenticar agote hilos
    # del proceso (seccion 63 del encargo).
    sol_broker_max_concurrent_connections: int = 200
    # Fase 5 / Bloque 5.2, Domain E2: interruptor explicito de arranque del
    # broker DENTRO del proceso backend (ver `app/broker/product_startup.py`).
    # `False` por defecto -- deliberadamente NUNCA activado en el
    # `docker-compose.yml` de este entorno de desarrollo/pruebas todavia
    # (no hay material de CA provisto en este host, y activarlo sin CA
    # rompería el arranque del backend en fallo cerrado). Un despliegue real
    # que ya provisiono `sol_broker_ca_dir` con una CA real debe activarlo
    # explicitamente via la variable de entorno `SOL_BROKER_ENABLED=true`.
    sol_broker_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
