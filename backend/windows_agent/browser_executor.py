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

import json
import dataclasses
import re
import time
from dataclasses import replace
from collections.abc import Callable
from contextlib import AbstractContextManager
from logging import Logger, getLogger

from windows_agent import destination_contract as dc
from windows_agent import auth_trace, diagnostics, interstitial
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
    DETAIL_LANDING_IDENTITY,
    DETAIL_LANDING_TRANSIT,
    _host as _host_of,
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
      maxlength: e.getAttribute('maxlength'), visible: vis(e), classes: Array.from(e.classList), role: e.getAttribute('role'),
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


class _AutomationStopped(Exception):
    """Intento de clic con el presupuesto de navegacion agotado/congelado (defensa en profundidad)."""


class _NavigationBlocked(Exception):
    """El control a pulsar no es accionable porque otra cosa lo cubre (p. ej. un aviso/modal de SUNAT). NO se clico nada y el
    presupuesto no se consumio; el humano debe resolver el aviso. `description` solo contiene etiqueta/id/clase estructurales."""

    def __init__(self, description: str = "") -> None:
        super().__init__(description)
        self.description = description


_BLOCKER_RE = re.compile(r"<(?P<tag>[a-zA-Z][a-zA-Z0-9]{0,15})(?P<attrs>[^>]{0,400})>[\s\S]{0,2000}?intercepts pointer events")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def describe_blocker(exc: BaseException) -> str:
    """Resumen ESTRUCTURAL y saneado de lo que cubre el control (`tag#id.clase`), sin texto del portal. '' si no se reconoce."""
    match = _BLOCKER_RE.search(str(exc))
    if not match:
        return ""
    attrs = match.group("attrs")
    parts = [match.group("tag").lower()]
    ident = re.search(r'(?:^|\s)id="([^"]{1,80})"', attrs)
    if ident and _TOKEN_RE.match(ident.group(1)):
        parts.append("#" + ident.group(1))
    klass = re.search(r'(?:^|\s)class="([^"]{1,200})"', attrs)
    if klass:
        parts.extend("." + c for c in klass.group(1).split()[:4] if _TOKEN_RE.match(c))
    return "".join(parts)[:120]


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
        landing_hop_grace_seconds: float = 15.0,
        auth_navigation_timeout_seconds: float = 60.0,
        login_ready_wait_seconds: float = 5.0,
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
        self._hop_grace = landing_hop_grace_seconds
        self._auth_nav_timeout = auth_navigation_timeout_seconds
        self._ready_wait = login_ready_wait_seconds
        self._submit_state: dict | None = None
        self._transit_observation: dict | None = None
        self._trace: auth_trace.AuthTrace | None = None
        self._auth_document: dict | None = None
        self._titles: dict[int, str] = {}  # ultimo titulo leido por Page (para sondear sin bloquearse durante una navegacion pendiente)
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
        # M2: presupuesto de clics de NAVEGACION a un destino (solo tras el marcador positivo del shell). Agotado o
        # congelado => cualquier clic posterior cuenta como `post_landing_clicks` (debe ser 0) y se rechaza.
        self._nav_budget = 0
        self._trial_click_ms = 2500
        self._last_action = "NONE"
        self._timeline: list[dict] = []
        self._timeline_t0: float | None = None
        self._last_views: tuple = ()
        self._announced_waiting = False
        self._waiting_reason: str | None = None
        self._destination_clicks = 0

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
            if self._nav_budget > 0:
                self._nav_budget -= 1
                self._destination_clicks += 1
            else:
                self._post_landing_clicks += 1

    @property
    def mode_activation_clicks(self) -> int:
        return self._mode_clicks

    @property
    def post_landing_clicks(self) -> int:
        return self._post_landing_clicks

    # --- M2: navegacion acotada a un destino validado ---

    @property
    def destination_clicks(self) -> int:
        return self._destination_clicks

    def arm_destination_navigation(self, max_clicks: int) -> None:
        """Solo tras el marcador positivo del shell. Fija el presupuesto TOTAL de clics permitidos hacia el destino."""
        if not self._landed:
            raise RuntimeError("navigation requires an authenticated landing")
        self._nav_budget = max(0, int(max_clicks))

    def freeze_automation(self) -> None:
        """Destino validado: la automatizacion TERMINA. Ningun clic posterior (presupuesto 0)."""
        self._nav_budget = 0

    def guarded_click(self, locator) -> None:
        """Unico camino de clic post-aterrizaje: rechaza (sin clicar) si el presupuesto esta agotado/congelado.

        Antes de clicar hace una comprobacion de accionabilidad SIN efectos (`trial=True`: no pulsa nada). Si algo cubre el control
        (aviso/modal) lanza `_NavigationBlocked` sin consumir presupuesto, en lugar de esperar 30 s y fallar con un timeout."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if self._nav_budget <= 0:
            raise _AutomationStopped()
        self._checkpoint()
        try:
            locator.click(trial=True, timeout=self._trial_click_ms)
            locator.click(timeout=self._trial_click_ms * 4)
        except PlaywrightTimeoutError as exc:
            raise _NavigationBlocked(describe_blocker(exc)) from None
        self._count_click()

    @property
    def last_action(self) -> str:
        """Ultima accion SEGURA completada (etiqueta fija; nunca contenido del portal)."""
        return self._last_action

    def set_last_action(self, name: str) -> None:
        self._last_action = name

    def _observe_entry(self, page) -> dict:
        c = self._contract
        heading = page.get_by_role("heading", name=re.compile(rf"^\s*{re.escape(c.entry_heading_text)}\s*$", re.I))
        count = heading.count()
        links = 0
        if count == 1:
            links = heading.locator("xpath=following-sibling::*[1]").get_by_role("link", name=c.entry_link_text, exact=True).count()
        return {"heading_count": count, "link_count": links}

    def observe(self) -> dict:
        """Observacion ESTRUCTURAL saneada (solo contadores/booleanos; ver `diagnostics`). Lectura pura: ningun clic. Nunca lanza."""
        try:
            c = self._contract
            pages = self._pages()
            views = tuple(self._view(p) for p in pages)
            obs: dict = {
                "page_count": len(views),
                "frame_count": sum(len(v.frame_urls) for v in views),
                "pages": diagnostics.observe_pages(views, c, _host_of),
                "surface_kind": classify_surface(views, c, strict_auth_identity=False).kind.name,
                "timeline": list(self._timeline),
            }
            if self._transit_observation is not None:
                obs["auth_transit"] = self._transit_observation
            obs.update(self._trace_observation())
            for page, view in zip(pages, views):
                if diagnostics.host_class(_host_of(view.url)[1], c) == "landing" and "landing_shell" not in obs:
                    obs["landing_shell"] = self._shell_probe(page)[1]
            for page, view in zip(pages, views):
                klass = diagnostics.host_class(_host_of(view.url)[1], c)
                if klass == "auth" and "login" not in obs and not self._navigation_pending():
                    obs["login"] = diagnostics.observe_login_controls(self._login_snapshot(page), c)
                if klass == "entry" and "entry" not in obs:
                    obs["entry"] = self._observe_entry(page)
            return obs
        except Exception:  # noqa: BLE001 -- el diagnostico es opcional: jamas afecta a la sesion
            fallback: dict = {"observation_unavailable": True}
            if self._trace is not None:  # el navegador ya no esta: la traza acumulada en memoria sigue siendo evidencia
                summary = self._trace.summary()
                fallback.update({"auth_trace": summary, "timeline": list(self._timeline),
                                 "stall_reason": auth_trace.stall_reason(summary, self._auth_document)})
                if self._auth_document is not None:
                    fallback["auth_document"] = self._auth_document
            return fallback

    @property
    def waiting_reason(self) -> str | None:
        """Motivo (categoria cerrada) de la espera humana: CONTACT_VALIDATION_INTERSTITIAL, INFORMATIONAL_MODAL o HUMAN_ACTION_REQUIRED."""
        return self._waiting_reason

    def classify_blocker(self, page) -> str:
        """Lectura de SOLO LECTURA de la pantalla intermedia que cubre el menu. Registra solo estructura y palabras del vocabulario cerrado."""
        try:
            observation = interstitial.sanitize(page.evaluate(interstitial.INTERSTITIAL_JS))
        except Exception:  # noqa: BLE001 -- pagina en navegacion: no se pudo leer; sigue requiriendo al humano
            return interstitial.HUMAN_ACTION_REQUIRED
        category = interstitial.classify(observation)
        log_event(self._logger, "interstitial_observed", category=category, dialogs=len(observation["dialogs"]),
                  observation=json.dumps(observation, sort_keys=True)[:1500])
        return category

    def announce_waiting_for_user(self, reason: str | None = None) -> None:
        """Informa (una sola vez) que SUNAT requiere una accion HUMANA antes de seguir; el navegador permanece abierto."""
        if reason in interstitial.WAITING_REASONS:
            self._waiting_reason = reason
        if not self._announced_waiting:
            self._announced_waiting = True
            log_event(self._logger, "auth_waiting_for_user")
            self._on_state(AuthOutcome.WAITING_FOR_USER)

    @property
    def human_wait_seconds(self) -> float:
        return self._challenge_timeout

    def current_pages(self) -> list:
        return self._pages()

    def id_snapshot(self, page, ids: tuple[str, ...]) -> LoginSnapshot:
        return self._id_snapshot(page, ids)

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
        self._trace = auth_trace.AuthTrace(self._contract, self._clock, _host_of)
        self._trace.attach(self._context)  # solo escucha eventos: no intercepta ni altera ninguna respuesta
        self._last_action = "BROWSER_LAUNCHED"

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

    def _pump(self, seconds: float) -> None:
        """Espera ENTREGANDO los eventos de Playwright sin tocar el documento (no se bloquea con una navegacion pendiente). Con la API
        sincrona `time.sleep` no los procesa: la traza y los cierres de Page quedarian obsoletos."""
        try:
            pages = [p for p in self._context.pages if not p.is_closed()] if self._context is not None else []
        except Exception:  # noqa: BLE001
            pages = []
        for page in pages:  # la primera Page utilizable (una caida o cierre no debe impedir entregar los eventos)
            try:
                page.wait_for_timeout(int(seconds * 1000))
                return
            except Exception:  # noqa: BLE001 -- esa Page desaparecio: se prueba la siguiente
                continue
        self._sleep(seconds)

    def _navigation_pending(self) -> bool:
        return self._trace is not None and self._trace.navigation_pending()

    def _view(self, page) -> PageView:
        c = self._contract
        if self._trace is not None and id(page) in self._trace.crashed:
            raise _BrowserGone()  # el renderizador de esa Page cayo: no es un navegador utilizable
        try:
            frame_urls = tuple(f.url for f in page.frames if f != page.main_frame)
            if self._navigation_pending():
                # Navegacion del frame principal sin respuesta: `title()`/localizadores se bloquearian hasta que llegue (o venza el plazo
                # de Playwright) y los plazos del agente (estancamiento, cancelacion) dejarian de ser reales. Solo datos en cache.
                return PageView(url=page.url, title=self._titles.get(id(page), ""), frame_urls=frame_urls)
            if self._submissions >= 1:
                # Tras el envio la pagina puede iniciar una navegacion en cualquier instante: `count()`/`all()` NO tienen plazo y se bloquean
                # hasta que esa navegacion llega (hasta ~25 s: capturado con un volcado de pila). Solo lecturas ACOTADAS (`wait_for`).
                password_visible = self._present(page.locator(f"#{c.password_field.element_id}:visible"))
                invalid = any(self._present(page.get_by_text(text)) for text in c.invalid_credentials_texts)
                challenge = self._challenge_visible(page)
            else:
                pwd = page.locator(f"#{c.password_field.element_id}")
                password_visible = pwd.count() > 0 and pwd.first.is_visible()
                invalid = any(page.get_by_text(text).count() > 0 for text in c.invalid_credentials_texts)
                challenge = False
            title = page.title()
            self._titles[id(page)] = title
            return PageView(
                url=page.url,
                title=title,
                frame_urls=frame_urls,
                password_field_visible=password_visible,
                invalid_credentials_visible=invalid,
                challenge_visible=challenge,
            )
        except Exception as exc:  # noqa: BLE001
            raise _BrowserGone() from exc

    _PRESENT_TIMEOUT_MS = 150

    def _present(self, locator) -> bool:
        """Lectura ACOTADA de existencia: `wait_for` respeta su plazo aunque el documento este en plena navegacion (a diferencia de
        `count()`/`all()`, que se bloquean). Un fallo o vencimiento significa "no presente"."""
        try:
            locator.first.wait_for(state="attached", timeout=self._PRESENT_TIMEOUT_MS)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _challenge_visible(self, page) -> bool:
        """Widget de verificacion humana POSITIVAMENTE identificado: un elemento del contrato, visible y de tamaño real. Lectura pura y acotada."""
        c = self._contract
        min_w, min_h = c.human_challenge_min_size
        try:
            for selector in c.human_challenge_selectors:
                locator = page.locator(f"{selector}:visible")
                if not self._present(locator):
                    continue
                for item in locator.all()[:6]:  # el documento ya respondio: no se bloquea
                    box = item.bounding_box(timeout=self._PRESENT_TIMEOUT_MS)
                    if box is not None and box["width"] >= min_w and box["height"] >= min_h:
                        return True
        except Exception:  # noqa: BLE001 -- pagina en navegacion: una lectura fallida NO identifica un desafio (y no es un navegador cerrado)
            return False
        return False

    def _trace_observation(self) -> dict:
        """Traza estructural de la autenticacion + estado del documento de la pagina de autenticacion (si sigue abierta) + motivo deducido."""
        if self._trace is None:
            return {}
        summary = self._trace.summary()
        out: dict = {"auth_trace": summary}
        if self._submit_state is not None:
            out["submit_state"] = dict(self._submit_state)
        document = self._auth_document
        try:
            for page in self._pages():
                if _host_of(page.url)[1] in self._contract.auth_hosts:
                    document = {"ready_state": "pending_navigation"} if self._navigation_pending() else auth_trace.read_document_state(page)
                    self._auth_document = document
                    break
        except Exception:  # noqa: BLE001
            pass
        if document is not None:
            out["auth_document"] = document
        out["stall_reason"] = auth_trace.stall_reason(summary, document)
        return out

    def _transit_probe(self, page) -> dict:
        """Estructura de una pagina de autenticacion que no avanza: SOLO contadores (nada de texto, URL ni valores)."""
        def count(selector: str) -> int:
            try:
                return page.locator(selector).count()
            except Exception:  # noqa: BLE001
                return -1

        return {
            "visible_inputs": count("input:visible"), "visible_buttons": count("button:visible, input[type=button]:visible, input[type=submit]:visible"),
            "visible_links": count("a:visible"), "forms": count("form"), "iframes": count("iframe"), "meta_refresh": count("meta[http-equiv=refresh i]"),
            "headings": count("h1, h2, h3"), "images": count("img:visible"),
        }

    def _surface(self, *, strict: bool = True) -> tuple[Surface, list]:
        pages = self._pages()
        if self._submissions >= 1:
            self._pump(0.02)  # entrega antes los eventos pendientes (p. ej. una navegacion recien solicitada) para no leer el documento a ciegas
        views = tuple(self._view(p) for p in pages)
        self._last_views = views
        return classify_surface(views, self._contract, strict_auth_identity=strict), pages

    def _shell_probe(self, page):
        """Marcador POSITIVO del shell autenticado sobre una pagina del host de aterrizaje (lectura pura). Si no se puede evaluar, NO es shell."""
        try:
            snapshot = self._id_snapshot(page, dc.SHELL_SNAPSHOT_IDS)
            check = dc.check_shell_marker(snapshot)
            return check, diagnostics.observe_shell(snapshot, check)
        except Exception:  # noqa: BLE001 -- pagina en navegacion: no confirma un shell autenticado
            return dc.ShellCheck(False, "shell probe unavailable"), {"marker_ok": False, "probe": "unavailable"}

    def _record_surface(self, surface, shell: dict | None = None) -> None:
        """Linea de tiempo SANEADA de la clasificacion de superficie (solo cambios): clase de host y booleanos; sin URLs ni textos."""
        now = self._clock()
        if self._timeline_t0 is None:
            self._timeline_t0 = now
        detail = None
        if surface.kind is SurfaceKind.FAIL:
            detail = diagnostics.classify(surface.detail, AuthOutcome.PORTAL_CHANGED)[1]
        entry = {
            "kind": surface.kind.name,
            "detail": detail,
            "pages": diagnostics.surface_signature(self._last_views, self._contract, _host_of),
        }
        if shell is not None:
            entry["shell"] = {"marker_ok": shell.get("marker_ok"), "ids": sorted(shell.get("ids_present", {}))}
        if self._timeline and {k: v for k, v in self._timeline[-1].items() if k != "t_ms"} == entry:
            return
        if len(self._timeline) < 40:
            self._timeline.append({"t_ms": int((now - self._timeline_t0) * 1000), **entry})

    # --- M3: entrada publica por TARJETA (p. ej. «Mis Declaraciones y Pagos»), configurada por el CONTRATO del destino, nunca por el frontend ---

    def configure_public_entry(self, *, heading_text: str, landing_path_prefix: str) -> None:
        """Cambia SOLO el encabezado de la tarjeta de entrada y el prefijo de ruta del landing (la plataforma `/cl-ti-itmenu2/`). Debe llamarse ANTES de
        abrir el navegador: tras `_launch` el contrato queda fijo (jamas se reconfigura una sesion en curso)."""
        if self._pw is not None or self._browser is not None or self._submissions:
            raise RuntimeError("entry can only be configured before the browser starts")
        if not heading_text.strip() or not landing_path_prefix.startswith("/") or not landing_path_prefix.endswith("/"):
            raise ValueError("invalid public entry configuration")
        self._contract = dataclasses.replace(
            self._contract, entry_heading_text=heading_text, entry_link_strategy="card_container", landing_path_prefix=landing_path_prefix,
            landing_observed_path=None,
        )

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
                self._last_action = "LOGIN_FORM_VALIDATED"
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
            self._last_action = "ENTRY_LOADED"
            link = self._entry_link(page, c)
            if link is None:
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="entry link not found")
            self._count_click()
            link.click()  # el login se abre en una Page NUEVA (LIVE-A1); la publica permanece
            self._last_action = "ENTRY_LINK_CLICKED"
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
        if c.entry_link_strategy == "card_container":
            # ancestro MAS CERCANO del encabezado que contiene algun enlace «Ingresar» (insensible a mayusculas; el texto en mayusculas es CSS)
            container = heading.locator("xpath=ancestor::*[.//a[translate(normalize-space(.), 'INGRESAR', 'ingresar') = 'ingresar']][1]")
            if container.count() != 1:
                return None
            links = container.get_by_role("link", name=re.compile(rf"^\s*{re.escape(c.entry_link_text)}\s*$", re.I))
            return links if links.count() == 1 else None  # 0 o varios => ambiguo: falla cerrado
        link = heading.locator("xpath=following-sibling::*[1]").get_by_role("link", name=c.entry_link_text, exact=True)
        return link if link.count() == 1 else None

    def _await_login_surface(self) -> AuthResult | None:
        c = self._contract
        deadline = self._clock() + self._nav_timeout_ms / 1000
        pending_since: float | None = None
        while self._clock() < deadline:
            self._checkpoint()
            surface, pages = self._surface()
            self._record_surface(surface)
            transit_unresolved_detail: str | None = None
            if surface.kind is SurfaceKind.LANDING:
                # Host + ruta + titulo del Menu SOL NO equivalen a un shell autenticado: el portal real pasa por una pagina del host de
                # aterrizaje (ya con ese titulo) ANTES de redirigir al login. Solo el marcador DOM positivo del shell decide:
                #  - shell autenticado completo ANTES de entregar credenciales => sesion inesperada: fallo cerrado;
                #  - sin marcador => tránsito pendiente (observacion pasiva y acotada; sin clics ni credenciales).
                check, shell = self._shell_probe(pages[surface.page_index])
                self._record_surface(surface, shell=shell)
                if check.ok:
                    return make_result(AuthOutcome.PORTAL_CHANGED, detail="landing before authentication")
                transit_unresolved_detail = DETAIL_LANDING_TRANSIT
            if surface.kind is SurfaceKind.PENDING or transit_unresolved_detail is not None:
                # estado B: transito de autenticacion pendiente (pagina del host de aterrizaje que aun no es ni login ni shell autenticado)
                pending_since = pending_since if pending_since is not None else self._clock()
                if self._clock() - pending_since >= self._hop_grace:
                    return make_result(AuthOutcome.PORTAL_CHANGED, detail=transit_unresolved_detail or DETAIL_LANDING_IDENTITY)
                self._pump(self._poll)
                continue
            pending_since = None
            if surface.kind is SurfaceKind.FAIL:
                return make_result(surface.outcome, detail=surface.detail)
            if surface.kind is SurfaceKind.LOGIN:
                return self._prepare_login_form(pages[surface.page_index], c)
            self._pump(self._poll)
        return make_result(AuthOutcome.TRANSIENT_NAVIGATION_FAILURE, detail="login surface not reached")

    def _login_snapshot(self, page) -> LoginSnapshot:
        """Metadatos NO secretos por id esperado (nunca el valor de un campo)."""
        return self._id_snapshot(page, self._contract.login_field_ids())

    def _id_snapshot(self, page, ids: tuple[str, ...]) -> LoginSnapshot:
        raw = page.evaluate(_LOGIN_SNAPSHOT_JS, list(ids))
        return LoginSnapshot(
            {
                element_id: tuple(
                    ElementInfo(
                        tag=e["tag"], type=e["type"], placeholder=e["placeholder"], maxlength=e["maxlength"],
                        visible=e["visible"], classes=tuple(e["classes"]), name_text=e["nameText"], role=e.get("role"),
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
            self._pump(self._poll)

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

    def _wait_login_document_ready(self, page) -> dict:
        """Espera acotada a `document.readyState == 'complete'` de la pagina de login ANTES de escribir la credencial. Registra el estado observado y el
        tiempo esperado (solo un enum y un numero). Nunca falla la sesion: si vence el plazo continua con el flujo de siempre."""
        started = self._clock()
        state = "unknown"
        while True:
            try:
                state = page.evaluate("document.readyState")
            except Exception:  # noqa: BLE001 -- pagina en navegacion
                state = "unavailable"
                break
            if state == "complete" or self._clock() - started >= self._ready_wait:
                break
            self._pump(self._poll)
        if state not in {"loading", "interactive", "complete", "unavailable"}:
            state = "unknown"
        return {"ready_state": state, "waited_ms": int((self._clock() - started) * 1000)}

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
            # un usuario no envia el formulario antes de que la pagina termine de cargar: el login real inicializa sus scripts en `load`. Espera ACOTADA;
            # vencida, se envia como antes (la compuerta nunca bloquea la sesion ni reenvia nada).
            self._submit_state = self._wait_login_document_ready(page)
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
            if self._trace is not None:
                self._trace.mark_submitted()
            self._count_click()
            page.locator(f"#{c.submit_button.element_id}").click()
            self._last_action = "CREDENTIAL_SUBMITTED"
        except _Cancelled:
            credential._clear()
            return make_result(AuthOutcome.CANCELLED, submissions=self._submissions)
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
        """Tras el envio distingue TRES situaciones en una pagina de autenticacion que no es el landing:
        * AUTH_NAVIGATION_PENDING: carga/redireccion en curso (sin desafio positivo). Observacion pasiva y plazo acotado
          (`auth_navigation_timeout_seconds`); si vence => `AUTH_NAVIGATION_STALLED` (terminal, nueva sesion supervisada).
        * HUMAN_ACTION_REQUIRED: desafio humano identificado POSITIVAMENTE (widget visible del contrato). Unico caso de `WAITING_FOR_USER`.
        * credenciales invalidas visibles => terminal inmediato.
        Nunca se pulsa nada ni se reenvia la credencial."""
        waiting_since: float | None = None
        pending_since: float | None = None
        nav_since: float | None = None
        subs = self._submissions
        while True:
            try:
                self._checkpoint()
                surface, pages = self._surface(strict=False)
                self._record_surface(surface)
            except _Cancelled:
                return make_result(AuthOutcome.CANCELLED, submissions=subs)
            except _BrowserGone:
                return make_result(AuthOutcome.BROWSER_FAILURE, submissions=subs)
            now = self._clock()
            if surface.kind is SurfaceKind.PENDING:
                # tras enviar: salto intermedio hacia el menu aun cargando. No es el landing ni un fallo; espera acotada.
                pending_since = pending_since if pending_since is not None else now
                if now - pending_since >= max(self._hop_grace, self._settle_grace):
                    return make_result(AuthOutcome.PORTAL_CHANGED, detail=DETAIL_LANDING_IDENTITY, submissions=subs)
                self._pump(self._poll)
                continue
            pending_since = None
            if surface.kind is SurfaceKind.LANDING:
                self._landed = True
                log_event(self._logger, "auth_landing_reached")
                self._last_action = "LANDING_REACHED"
                return make_result(AuthOutcome.AUTH_SUCCEEDED, submissions=subs)  # la automatizacion TERMINA aqui
            if surface.kind is SurfaceKind.FAIL:
                return make_result(surface.outcome, detail=surface.detail, submissions=subs)
            if surface.kind is SurfaceKind.NO_PAGES:
                return make_result(AuthOutcome.BROWSER_FAILURE, submissions=subs)
            if surface.kind is SurfaceKind.ENTRY:
                return make_result(AuthOutcome.PORTAL_CHANGED, detail="returned to entry page", submissions=subs)
            # pagina de autenticacion tras el envio
            if surface.invalid_credentials:
                return make_result(AuthOutcome.INVALID_CREDENTIALS, submissions=subs)
            if surface.human_challenge:
                nav_since = None
                if waiting_since is None:
                    waiting_since = now
                    self.announce_waiting_for_user("HUMAN_ACTION_REQUIRED")
                elif now - waiting_since >= self._challenge_timeout:
                    return make_result(AuthOutcome.EXPIRED, submissions=subs)
            else:
                # AUTH_NAVIGATION_PENDING (tambien si el desafio humano ya no esta): ninguna accion humana validada
                if waiting_since is not None:
                    self._announced_waiting = False  # el desafio desaparecio: un nuevo motivo humano se anunciaria de nuevo
                waiting_since = None
                nav_since = nav_since if nav_since is not None else now
                if now - nav_since >= self._auth_nav_timeout:
                    try:
                        auth_pages = [p for p in pages if _host_of(p.url)[1] in self._contract.auth_hosts]
                        if auth_pages and not self._navigation_pending():
                            self._transit_observation = self._transit_probe(auth_pages[0])
                            self._auth_document = auth_trace.read_document_state(auth_pages[0])
                        elif auth_pages:
                            self._auth_document = {"ready_state": "pending_navigation"}
                    except Exception:  # noqa: BLE001 -- la observacion es opcional
                        pass
                    return make_result(AuthOutcome.PORTAL_CHANGED, detail="auth navigation stalled", submissions=subs)
            self._pump(self._poll)


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
        if result.outcome not in {AuthOutcome.AUTH_SUCCEEDED, AuthOutcome.CANCELLED}:
            result = diagnostics.annotate(result, observation=diagnostics.observation_of(executor), last_action=diagnostics.last_action_of(executor))
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
