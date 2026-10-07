"""Fase 5, Bloque 5.2, Domain E3 -- ejecutor de navegador supervisado
(`AUTHENTICATE_TO_LANDING_ONLY`).

Capas: runtime -> `run_authentication` (frontera de credencial) -> ejecutor
(Playwright) -> reglas de identidad (`portal_identity`, `portal_contract`).

Invariantes (cada una con prueba):

- navegador VISIBLE, `BrowserContext` EFIMERO (`chromium.launch` sin
  `user_data_dir`; sin `storage_state`, sin trazas, sin video, sin capturas,
  descargas deshabilitadas); contexto y navegador SIEMPRE se destruyen al
  terminar (exito, fallo o cancelacion);
- la credencial se pide UNA vez, solo cuando el formulario de login ya fue
  identificado y la sesion no esta cancelada; se escribe en los campos, se
  marca el envio y se sueltan las referencias INMEDIATAMENTE;
- `credential_submissions <= 1` por sesion: tras el envio no hay reintento de
  login, ni reenvio, ni nueva solicitud de credencial (entrega de un solo uso);
- challenge humano: NUNCA se resuelve, ni OCR, ni IA, ni captura: se espera
  (`WAITING_FOR_USER`) con tope y se re-evalua la identidad Page/Frame;
- exito = identidad de Page/Frame (host + ruta + titulo + ausencia de login),
  jamas solo trafico de red;
- landing reconocido => la automatizacion TERMINA: cero clics posteriores;
- estado desconocido => `PORTAL_CHANGED` (fail closed), sin selectores
  alternativos ni heuristicas;
- nunca se registran valores de campos ni cuerpos de pagina."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from collections.abc import Callable
from contextlib import AbstractContextManager
from logging import Logger, getLogger

from windows_agent.broker_client import BrokerProtocolError, CredentialFetchError, CredentialMaterial
from windows_agent.logging_setup import log_event
from windows_agent.portal_contract import (
    DETAIL_AUTH_HOST_CHANGED,
    DETAIL_CLEANUP_FAILED,
    DETAIL_CREDENTIAL_UNAVAILABLE,
    TERMINAL_REASON_BY_OUTCOME,
    AuthOutcome,
    AuthResult,
    PortalContract,
)
from windows_agent.portal_identity import (
    ElementInfo,
    LoginCheck,
    LoginSnapshot,
    PageView,
    Surface,
    SurfaceKind,
    check_login_controls,
    classify_surface,
)

_DETAIL_CODES = {DETAIL_AUTH_HOST_CHANGED, DETAIL_CREDENTIAL_UNAVAILABLE, DETAIL_CLEANUP_FAILED}

# Instantanea NO secreta de los controles por id (nunca lee `value`). `visible` = caja con tamano
# y sin display/visibility ocultos. Un id duplicado devuelve varios elementos (ambiguedad).
_LOGIN_SNAPSHOT_JS = r"""
(ids) => {
  const out = {};
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const id of ids) {
    out[id] = Array.from(document.querySelectorAll('[id="' + id + '"]')).map(e => ({
      tag: e.tagName.toLowerCase(), type: e.getAttribute('type'), placeholder: e.getAttribute('placeholder'),
      maxlength: e.getAttribute('maxlength'), visible: vis(e), classes: Array.from(e.classList),
      nameText: ((e.getAttribute('aria-label') || e.innerText || '') + '').trim().replace(/\s+/g, ' ')
    }));
  }
  return out;
}
"""


def make_result(outcome: AuthOutcome, *, detail: str | None = None, submissions: int = 0) -> AuthResult:
    code = detail if detail in _DETAIL_CODES else TERMINAL_REASON_BY_OUTCOME[outcome]
    return AuthResult(outcome, terminal_reason_code=code, detail=detail, credential_submissions=submissions)


class _BrowserGone(Exception):
    """El navegador/pagina desaparecio (cierre por el usuario, crash)."""


class _Cancelled(Exception):
    pass


class SupervisedAuthExecutor:
    def __init__(
        self,
        contract: PortalContract,
        *,
        headless: bool = False,
        navigation_timeout_seconds: float = 30.0,
        settle_grace_seconds: float = 8.0,
        challenge_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.25,
        controls_wait_seconds: float = 5.0,
        pre_credential_attempts: int = 2,
        is_cancelled: Callable[[], bool] = lambda: False,
        tick: Callable[[], None] = lambda: None,
        on_state: Callable[[AuthOutcome], None] = lambda state: None,
        logger: Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._contract = contract
        self._headless = headless
        self._nav_timeout_ms = int(navigation_timeout_seconds * 1000)
        self._settle_grace = settle_grace_seconds
        self._challenge_timeout = challenge_timeout_seconds
        self._poll = poll_interval_seconds
        self._controls_wait = controls_wait_seconds
        self._attempts = max(1, pre_credential_attempts)
        self._is_cancelled = is_cancelled
        self._tick = tick
        self._on_state = on_state
        self._logger = logger or getLogger("windows_agent")
        self._clock = clock
        self._sleep = sleep

        self._pw = None
        self._browser = None
        self._context = None
        self._submissions = 0
        self._clicks = 0  # clics de entrada/login; ninguno posterior al landing
        self._landed = False
        self._mode_clicks = 0
        self._post_landing_clicks = 0

    # --- propiedades de auditoria ---

    @property
    def credential_submissions(self) -> int:
        return self._submissions

    @property
    def is_cancelled(self) -> bool:
        return bool(self._is_cancelled())

    def _count_click(self) -> None:
        self._clicks += 1
        if self._landed:
            self._post_landing_clicks += 1

    @property
    def mode_activation_clicks(self) -> int:
        return self._mode_clicks

    @property
    def post_landing_clicks(self) -> int:
        return self._post_landing_clicks

    # --- ciclo de vida del navegador ---

    def _launch(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # Visible y NO persistente: sin user_data_dir (perfil temporal de Playwright,
        # eliminado al cerrar), sin storage_state, sin traza/video, sin descargas.
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(accept_downloads=False, service_workers="block")
        self._context.set_default_timeout(self._nav_timeout_ms)
        self._context.set_default_navigation_timeout(self._nav_timeout_ms)

    def close(self) -> bool:
        """Cierre acotado y a prueba de fallos. Devuelve False si algun paso
        de limpieza fallo (el llamador lo trata como CLEANUP_FAILED)."""
        ok = True
        for name, obj, method in (
            ("context", self._context, "close"),
            ("browser", self._browser, "close"),
            ("playwright", self._pw, "stop"),
        ):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception:  # noqa: BLE001 -- limpieza: nunca propaga ni registra contenido
                ok = False
                log_event(self._logger, "browser_cleanup_step_failed", step=name)
        self._context = self._browser = self._pw = None
        return ok

    # --- lectura de la superficie (Page/Frame como fuente de verdad) ---

    def _checkpoint(self) -> None:
        self._tick()
        if self._is_cancelled():
            raise _Cancelled()

    def _pages(self) -> list:
        if self._browser is None or not self._browser.is_connected():
            raise _BrowserGone()
        pages = [p for p in self._context.pages if not p.is_closed()]
        return pages

    def _view(self, page) -> PageView:
        c = self._contract
        try:
            frame_urls = tuple(f.url for f in page.frames if f != page.main_frame)
            pwd = page.locator(f"#{c.password_field.element_id}")
            password_visible = pwd.count() > 0 and pwd.first.is_visible()
            invalid = any(page.get_by_text(t).count() > 0 for t in c.invalid_credentials_texts)
            return PageView(
                url=page.url,
                title=page.title(),
                frame_urls=frame_urls,
                password_field_visible=password_visible,
                invalid_credentials_visible=invalid,
            )
        except Exception as exc:  # noqa: BLE001
            raise _BrowserGone() from exc

    def _surface(self, *, strict: bool = True) -> tuple[Surface, list]:
        pages = self._pages()
        views = tuple(self._view(p) for p in pages)
        return classify_surface(views, self._contract, strict_auth_identity=strict), pages

    # --- fase 1: abrir la entrada e identificar el login (SIN credenciales) ---

    def open_and_identify_login(self) -> AuthResult | None:
        """None => formulario de login identificado y listo. AuthResult =>
        terminal (nunca se llego a pedir la credencial). Reintentos acotados
        SOLO para fallos transitorios previos a la credencial."""
        last: AuthResult | None = None
        for attempt in range(self._attempts):
            try:
                self._checkpoint()
                self._launch()
                result = self._navigate_to_login()
            except _Cancelled:
                return make_result(AuthOutcome.CANCELLED)
            except _BrowserGone:
                result = make_result(AuthOutcome.BROWSER_FAILURE)
            except Exception as exc:  # noqa: BLE001 -- fallo de arranque de Playwright/Chromium
                log_event(self._logger, "browser_launch_failed", error=type(exc).__name__)
                result = make_result(AuthOutcome.BROWSER_FAILURE)
            if result is None:
                return None
            last = result
            if result.outcome not in {AuthOutcome.TRANSIENT_NAVIGATION_FAILURE, AuthOutcome.BROWSER_FAILURE}:
                return result
            self.close()  # estado limpio antes del siguiente intento acotado
            if attempt + 1 < self._attempts:
                log_event(self._logger, "pre_credential_retry", attempt=attempt + 1)
        return last

    def _navigate_to_login(self) -> AuthResult | None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        c = self._contract
        try:
            page = self._context.new_page()
            page.goto(c.entry_url, wait_until="domcontentloaded")
            link = self._entry_link(page, c)
            if link is None:
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="entry link not found")
            self._count_click()
            link.click()  # el login se abre en una Page NUEVA (LIVE-A1); la publica permanece
            return self._await_login_surface()
        except PlaywrightTimeout:
            return make_result(AuthOutcome.TRANSIENT_NAVIGATION_FAILURE, detail="navigation timeout")
        except PlaywrightError as exc:
            raise _BrowserGone() from exc

    @staticmethod
    def _entry_link(page, c: PortalContract):
        """Enlace de entrada por RELACION ESTRUCTURAL (nunca por indice global): encabezado
        (rol heading, texto DOM exacto sin distinguir mayusculas) -> contenedor hermano SIGUIENTE
        -> enlace. Exige exactamente un encabezado y exactamente un enlace."""
        heading = page.get_by_role("heading", name=re.compile(rf"^\s*{re.escape(c.entry_heading_text)}\s*$", re.I))
        if heading.count() != 1:
            return None
        link = heading.locator("xpath=following-sibling::*[1]").get_by_role("link", name=c.entry_link_text, exact=True)
        return link if link.count() == 1 else None

    def _await_login_surface(self) -> AuthResult | None:
        c = self._contract
        deadline = self._clock() + self._nav_timeout_ms / 1000
        while self._clock() < deadline:
            self._checkpoint()
            surface, pages = self._surface()
            if surface.kind is SurfaceKind.FAIL:
                return make_result(surface.outcome, detail=surface.detail)
            if surface.kind is SurfaceKind.LANDING:
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="landing before authentication")
            if surface.kind is SurfaceKind.LOGIN:
                return self._prepare_login_form(pages[surface.page_index], c)
            self._sleep(self._poll)
        return make_result(AuthOutcome.TRANSIENT_NAVIGATION_FAILURE, detail="login surface not reached")

    def _login_snapshot(self, page) -> LoginSnapshot:
        """Metadatos NO secretos por id esperado (nunca el valor de un campo)."""
        raw = page.evaluate(_LOGIN_SNAPSHOT_JS, list(self._contract.login_field_ids()))
        return LoginSnapshot(
            {
                element_id: tuple(
                    ElementInfo(
                        tag=e["tag"], type=e["type"], placeholder=e["placeholder"], maxlength=e["maxlength"],
                        visible=e["visible"], classes=tuple(e["classes"]), name_text=e["nameText"],
                    )
                    for e in infos
                )
                for element_id, infos in raw.items()
            }
        )

    def _check_controls(self, page) -> tuple[LoginCheck, str | None]:
        """Espera ACOTADA a que el DOM del login se asiente; fuera de ese margen, fail closed."""
        deadline = self._clock() + self._controls_wait
        while True:
            check, detail = check_login_controls(self._login_snapshot(page), self._contract)
            if check is not LoginCheck.FAIL or self._clock() >= deadline:
                return check, detail
            self._sleep(self._poll)

    def _prepare_login_form(self, page, c: PortalContract) -> AuthResult | None:
        """Valida la identidad de TODOS los controles (y el modo RUC) ANTES de pedir la
        credencial; NUNCA busca alternativos. Modo RUC ya activo => no se hace clic; inactivo =>
        UN clic de activacion sobre el mismo `#btnPorRuc` y revalidacion estricta."""
        check, detail = self._check_controls(page)
        if check is LoginCheck.FAIL:
            return make_result(AuthOutcome.PORTAL_CHANGED, detail=detail)
        if check is LoginCheck.NEEDS_RUC_ACTIVATION:
            if self._mode_clicks >= 1:  # defensa: maximo 1 clic de activacion
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="RUC mode activation already attempted")
            self._mode_clicks += 1
            self._count_click()
            page.locator(f"#{c.mode_ruc_button_id}").click()
            check, detail = self._check_controls(page)
            if check is not LoginCheck.READY:
                return make_result(AuthOutcome.PORTAL_CHANGED, detail=detail or "RUC mode activation failed")
        return None

    # --- fase 2: llenar y enviar UNA vez ---

    def submit_credentials(self, credential: CredentialMaterial) -> AuthResult | None:
        """Llena SOLO los campos requeridos y envia. Suelta las referencias a
        la credencial inmediatamente despues del envio. None => enviado."""
        c = self._contract
        try:
            self._checkpoint()
        except _Cancelled:
            credential._clear()
            return make_result(AuthOutcome.CANCELLED)
        if self._submissions >= 1:  # defensa en profundidad: nunca un segundo envio
            credential._clear()
            return make_result(AuthOutcome.BROWSER_FAILURE, detail="second submission refused", submissions=self._submissions)
        try:
            surface, pages = self._surface()
            if surface.kind is not SurfaceKind.LOGIN:
                credential._clear()
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="login surface changed before submit")
            page = pages[surface.page_index]
            # sin variables locales con secretos: se leen en la propia llamada
            check, _detail = check_login_controls(self._login_snapshot(page), c)
            if check is not LoginCheck.READY:  # revalidacion previa a escribir (nunca activa el modo aqui)
                credential._clear()
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="login controls changed before submit")
            page.locator(f"#{c.ruc_field.element_id}").fill(credential.ruc)
            page.locator(f"#{c.username_field.element_id}").fill(credential.sol_username)
            page.locator(f"#{c.password_field.element_id}").fill(credential.sol_password)
            self._submissions = 1  # se marca ANTES del clic: aunque el clic falle, no hay reenvio
            credential._clear()
            self._count_click()
            page.locator(f"#{c.submit_button.element_id}").click()
        except _BrowserGone:
            credential._clear()
            return make_result(AuthOutcome.BROWSER_FAILURE, submissions=self._submissions)
        except Exception as exc:  # noqa: BLE001
            credential._clear()
            # el mensaje de Playwright podria citar el selector; nunca se propaga ni se registra
            log_event(self._logger, "submit_failed", error=type(exc).__name__)
            return make_result(AuthOutcome.BROWSER_FAILURE, submissions=self._submissions)
        return None

    # --- fase 3: esperar el resultado (identidad Page/Frame) ---

    def await_outcome(self) -> AuthResult:
        grace_end = self._clock() + self._settle_grace
        waiting_since: float | None = None
        subs = self._submissions
        while True:
            try:
                self._checkpoint()
                surface, _pages = self._surface(strict=False)
            except _Cancelled:
                return make_result(AuthOutcome.CANCELLED, submissions=subs)
            except _BrowserGone:
                return make_result(AuthOutcome.BROWSER_FAILURE, submissions=subs)
            now = self._clock()
            if surface.kind is SurfaceKind.LANDING:
                self._landed = True
                log_event(self._logger, "auth_landing_reached")
                return make_result(AuthOutcome.AUTH_SUCCEEDED, submissions=subs)  # la automatizacion TERMINA aqui
            if surface.kind is SurfaceKind.FAIL:
                return make_result(surface.outcome, detail=surface.detail, submissions=subs)
            if surface.kind is SurfaceKind.NO_PAGES:
                return make_result(AuthOutcome.BROWSER_FAILURE, submissions=subs)
            if surface.kind is SurfaceKind.ENTRY:
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="returned to entry page", submissions=subs)
            # LOGIN sigue visible tras el envio
            if surface.invalid_credentials:
                return make_result(AuthOutcome.INVALID_CREDENTIALS, submissions=subs)
            if now >= grace_end:
                if waiting_since is None:
                    waiting_since = now
                    log_event(self._logger, "auth_waiting_for_user")
                    self._on_state(AuthOutcome.WAITING_FOR_USER)
                elif now - waiting_since >= self._challenge_timeout:
                    return make_result(AuthOutcome.EXPIRED, submissions=subs)
            self._sleep(self._poll)


def _authenticate(
    executor: SupervisedAuthExecutor,
    credential_provider: Callable[[], AbstractContextManager[CredentialMaterial]],
    preauth_ready: Callable[[], bool] | None = None,
) -> AuthResult:
    early = executor.open_and_identify_login()
    if early is not None:
        return early
    if executor.is_cancelled:  # cancelacion ANTES de entregar el secreto
        return make_result(AuthOutcome.CANCELLED)
    # Domain F: el portal ya esta validado por completo (aun SIN credencial). Se senala PREAUTH_READY al broker, que
    # revalida y autoriza JUST-IN-TIME la entrega; solo con su confirmacion se pide la credencial. Sin confirmacion:
    # el endpoint de credencial NO se toca.
    if preauth_ready is not None and not preauth_ready():
        return make_result(AuthOutcome.BROWSER_FAILURE, detail=DETAIL_CREDENTIAL_UNAVAILABLE)
    if executor.is_cancelled:  # cancelacion durante la autorizacion
        return make_result(AuthOutcome.CANCELLED)
    try:
        with credential_provider() as credential:
            early = executor.submit_credentials(credential)
    except (CredentialFetchError, BrokerProtocolError):
        # sin redelivery: se requiere una nueva sesion autorizada
        return make_result(AuthOutcome.BROWSER_FAILURE, detail=DETAIL_CREDENTIAL_UNAVAILABLE)
    if early is not None:
        return early
    return executor.await_outcome()


def run_authentication(
    executor: SupervisedAuthExecutor,
    credential_provider: Callable[[], AbstractContextManager[CredentialMaterial]],
    preauth_ready: Callable[[], bool] | None = None,
) -> AuthResult:
    """Frontera de credencial. `credential_provider` es
    `OperationalBrokerClient.fetch_credential_for_job` ya parcialmente aplicado:
    se invoca UNA sola vez, jamas se reintenta (entrega de un solo uso). El
    navegador se destruye SIEMPRE; si la limpieza falla, un exito deja de serlo
    (`CLEANUP_FAILED`)."""
    try:
        result = _authenticate(executor, credential_provider, preauth_ready)
    except BaseException:
        executor.close()
        raise
    cleaned = executor.close()
    result = replace(
        result, credential_submissions=executor.credential_submissions, post_landing_clicks=executor.post_landing_clicks
    )
    if not cleaned and result.outcome is AuthOutcome.AUTH_SUCCEEDED:
        return make_result(AuthOutcome.BROWSER_FAILURE, detail=DETAIL_CLEANUP_FAILED, submissions=executor.credential_submissions)
    return result
