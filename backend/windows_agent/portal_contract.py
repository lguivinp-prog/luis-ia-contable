"""Fase 5, Bloque 5.2, Domain E3 -- contrato de identidad del portal SOL y
vocabulario de resultados de la autenticacion supervisada.

ALCANCE EXACTO: `AUTHENTICATE_TO_LANDING_ONLY`. Este modulo SOLO describe
que paginas/hosts/controles son los esperados; no contiene logica de
navegacion.

FUENTES (nada se adivina):

* LIVE-A1 (reconocimiento publico de SOLO LECTURA, sin autenticar; ver
  `docs/phase5/block5_2/BLOCK5_2_WINDOWS_AGENT.md` seccion 12) -- hechos
  OBSERVADOS en el portal real:
  - entrada: 5 enlaces "Ingresar"; el correcto es el contenedor hermano
    siguiente del encabezado cuyo texto DOM es "Mis tramites y consultas" (el
    mayusculas es CSS `text-transform`); NUNCA se identifica por orden global;
  - el login abre en una Page NUEVA de nivel superior
    (`api-seguridad.sunat.gob.pe`, ruta `/v1/clientessol/<client-id>/oauth2/
    loginMenuSol`, `<client-id>` dinamico, titulo "SUNAT - Menú SOL"), sin
    frames de autenticacion; la Page publica original permanece en
    `www.sunat.gob.pe`;
  - controles por id (el portal NO usa `<label>` ni atributo `name`):
    `#txtRuc` (text, placeholder RUC, maxlength 11), `#txtUsuario` (text,
    placeholder Usuario, maxlength 8), `#txtContrasena` (password, placeholder
    Contraseña, maxlength 12), `#btnAceptar` (button type=button, texto
    "Iniciar sesión"); modo RUC ya activo por defecto (`#btnPorRuc` con clase
    `active`, `#txtRuc` visible, `#txtDni` oculto, `#btnPorDni` inactivo); NO
    existe un control "Ingresa por RUC";
  - `input#captcha` existe como `type=hidden` con contenedor no visible: NO es un
    challenge activo (`HIDDEN_CAPTCHA_INPUT_MEANS_CHALLENGE=NO`).
* Spike A (smoke manual real): destino post-login `e-menu.sunat.gob.pe`
  `/cl-ti-itmenu/AutenticaMenuInternet.htm` (la `originalUrl` del flujo OAuth).
* LIVE-B (UNA autenticacion real supervisada, resultado sanitizado) -- landing
  autenticado OBSERVADO como Page de nivel superior: `https://e-menu.sunat.gob.pe`
  `/cl-ti-itmenu/MenuInternet.htm`, titulo "SUNAT - Menú SOL", sin control de
  contraseña visible. El prefijo `/cl-ti-itmenu/` cubre ambas rutas (la de la
  `originalUrl` y la observada); NO se exige la ruta exacta para no rechazar un
  estado transitorio del redireccionamiento posterior al envio (sin reintento).
  El landing se reconoce por identidad de Page: esquema+host exacto+prefijo de
  ruta+titulo+AUSENCIA del formulario de login; la arquitectura no define un
  marcador estructural POSITIVO adicional y LIVE-B no registro ninguno.

SIGUEN `LIVE_UNVERIFIED`: el texto/marcador de credenciales invalidas, el
marcador visible de un challenge humano real y la identidad del landing bajo el
producto E3. El ejecutor falla cerrado (`PORTAL_CHANGED`) ante cualquier
diferencia estructural y NUNCA busca selectores alternativos."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class AuthOutcome(str, enum.Enum):
    AUTH_SUCCEEDED = "AUTH_SUCCEEDED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PORTAL_CHANGED = "PORTAL_CHANGED"
    TRANSIENT_NAVIGATION_FAILURE = "TRANSIENT_NAVIGATION_FAILURE"
    BROWSER_FAILURE = "BROWSER_FAILURE"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# Mapeo a `SolAccessTerminalReasonCode` (Domain C, ya existente -- ningun enum
# nuevo). `None` = no hay codigo terminal (exito, o estado no terminal).
TERMINAL_REASON_BY_OUTCOME: dict[AuthOutcome, str | None] = {
    AuthOutcome.AUTH_SUCCEEDED: None,
    AuthOutcome.WAITING_FOR_USER: None,
    AuthOutcome.INVALID_CREDENTIALS: "AUTH_FAILED",
    AuthOutcome.PORTAL_CHANGED: "PORTAL_CHANGED",
    AuthOutcome.TRANSIENT_NAVIGATION_FAILURE: "INTERNAL_ERROR",
    AuthOutcome.BROWSER_FAILURE: "BROWSER_CLOSED",
    AuthOutcome.CANCELLED: "CANCELLED_BY_USER",
    AuthOutcome.EXPIRED: "HUMAN_CHALLENGE_TIMEOUT",
}

# Detalles que afinan el codigo terminal sin crear enums nuevos.
DETAIL_AUTH_HOST_CHANGED = "AUTH_HOST_CHANGED"
DETAIL_WRONG_LOGIN_MODE = "WRONG_LOGIN_MODE"
DETAIL_CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
DETAIL_CLEANUP_FAILED = "CLEANUP_FAILED"


@dataclass(frozen=True)
class AuthResult:
    """Resultado de una autenticacion supervisada. NUNCA contiene
    credenciales ni contenido de campos; `terminal_reason_code` es un valor
    de `SolAccessTerminalReasonCode`."""

    outcome: AuthOutcome
    terminal_reason_code: str | None = None
    detail: str | None = None
    credential_submissions: int = 0
    post_landing_clicks: int = 0
    # M2 (navegacion a destino): clics de navegacion HACIA el destino (<= presupuesto del contrato); los clics
    # posteriores a la validacion del destino (`post_landing_clicks` en modo destino) deben ser SIEMPRE 0.
    destination_clicks: int = 0
    destination_reached: bool = False
    ficha_reported: bool = False
    # Diagnostico ESTRUCTURAL y saneado (ver `diagnostics`): etapa y codigo estable del fallo, observacion solo con contadores/booleanos.
    stage: str | None = None
    diagnostic_code: str | None = None
    observation: dict | None = None
    shell_reached: bool = False


@dataclass(frozen=True)
class FieldSpec:
    """Identidad estructural de un control de login (id exacto + atributos que lo
    corroboran). Si el id existe pero un atributo difiere => PORTAL_CHANGED."""

    element_id: str
    tag: str
    input_type: str | None
    placeholder: str | None
    maxlength: str | None = None


@dataclass(frozen=True)
class PortalContract:
    required_scheme: str
    entry_url: str
    entry_hosts: frozenset[str]
    auth_hosts: frozenset[str]
    # Patron de ruta del login (regex, anclado). El client-id es DINAMICO: nunca fijo.
    auth_path_pattern: str
    auth_title_contains: str
    landing_hosts: frozenset[str]
    landing_path_prefix: str
    landing_title_contains: str
    # Entrada publica: encabezado (por ROL, texto DOM, sin distinguir mayusculas solo por
    # CSS) y el enlace dentro del contenedor hermano SIGUIENTE a ese encabezado.
    entry_heading_text: str
    entry_link_text: str
    # Controles de login (por id; ver FieldSpec).
    ruc_field: FieldSpec
    username_field: FieldSpec
    password_field: FieldSpec
    submit_button: FieldSpec
    submit_name: str
    # Modo de login: el portal abre en modo RUC; solo si #btnPorRuc existe y el modo RUC esta
    # inactivo se permite UN clic de activacion.
    dni_field_id: str
    mode_ruc_button_id: str
    mode_dni_button_id: str
    mode_active_class: str
    # Textos de error de credenciales: LIVE_UNVERIFIED (vacio en produccion hasta confirmarlos
    # en vivo). Vacio => una pagina de login que persiste tras el envio se trata como
    # WAITING_FOR_USER (supervision humana), nunca como exito ni como reenvio.
    invalid_credentials_texts: tuple[str, ...] = ()
    # Hechos OBSERVADOS en LIVE-B (documentales: el reconocimiento sigue siendo por prefijo+titulo, ver el docstring).
    landing_observed_path: str | None = None
    landing_observed_title: str | None = None
    # Desafio humano POSITIVO tras el envio: elementos VISIBLES y de tamaño real que contienen un widget de verificacion. Sin uno de estos
    # (y sin texto de credenciales invalidas) una pagina de autenticacion que no avanza NO es una accion humana: es navegacion pendiente
    # (`AUTH_NAVIGATION_PENDING`) y, vencido el plazo, `AUTH_NAVIGATION_STALLED`. Lectura pura; jamas se pulsa nada.
    human_challenge_selectors: tuple[str, ...] = (
        "iframe[src*='captcha' i]:not([hidden])",
        "[id*='captcha' i]:not(input):not(script):not(.grecaptcha-badge)",
        "[class*='captcha' i]:not(input):not(script):not(.grecaptcha-badge)",
    )
    human_challenge_min_size: tuple[int, int] = (40, 30)

    def login_field_ids(self) -> tuple[str, ...]:
        return (
            self.ruc_field.element_id,
            self.username_field.element_id,
            self.password_field.element_id,
            self.dni_field_id,
            self.submit_button.element_id,
            self.mode_ruc_button_id,
            self.mode_dni_button_id,
        )


# Contrato de produccion -- ver el docstring del modulo (hechos LIVE-A1).
SOL_PORTAL_CONTRACT = PortalContract(
    required_scheme="https",
    entry_url="https://www.sunat.gob.pe/sol.html",
    entry_hosts=frozenset({"www.sunat.gob.pe", "sunat.gob.pe"}),
    auth_hosts=frozenset({"api-seguridad.sunat.gob.pe"}),
    auth_path_pattern=r"^/v1/clientessol/[^/]+/oauth2/loginMenuSol/?$",
    auth_title_contains="Menú SOL",
    landing_hosts=frozenset({"e-menu.sunat.gob.pe"}),
    landing_path_prefix="/cl-ti-itmenu/",
    landing_title_contains="Menú SOL",
    landing_observed_path="/cl-ti-itmenu/MenuInternet.htm",  # LIVE-B
    landing_observed_title="SUNAT - Menú SOL",  # LIVE-B
    entry_heading_text="Mis tramites y consultas",
    entry_link_text="Ingresar",
    ruc_field=FieldSpec("txtRuc", "input", "text", "RUC", "11"),
    username_field=FieldSpec("txtUsuario", "input", "text", "Usuario", "8"),
    password_field=FieldSpec("txtContrasena", "input", "password", "Contraseña", "12"),
    submit_button=FieldSpec("btnAceptar", "button", "button", None),
    submit_name="Iniciar sesión",
    dni_field_id="txtDni",
    mode_ruc_button_id="btnPorRuc",
    mode_dni_button_id="btnPorDni",
    mode_active_class="active",
    invalid_credentials_texts=(),
)


# Codigos de razon que el Agente puede reportar al broker (lista CERRADA, E3.1). Duplicada
# deliberadamente de `app.broker.protocol.AGENT_REPORTABLE_REASON_CODES` (el paquete del agente no
# importa `app.broker` en tiempo de ejecucion); una prueba verifica que ambas listas coinciden.
AGENT_REPORTABLE_REASON_CODES = (
    "AUTH_FAILED",
    "PORTAL_CHANGED",
    "AUTH_HOST_CHANGED",
    "WRONG_LOGIN_MODE",
    "HUMAN_CHALLENGE_TIMEOUT",
    "BROWSER_CLOSED",
    "CLEANUP_FAILED",
    "CREDENTIAL_UNAVAILABLE",
    "INTERNAL_ERROR",
)
