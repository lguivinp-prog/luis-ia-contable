"""M2 -- TRAZA ESTRUCTURAL y MINIMIZADA de la autenticacion (solo lectura; nunca altera ni intercepta una respuesta).

Responde a "por que la autenticacion no termina de navegar tras enviar la credencial" sin registrar nada sensible. Escucha los eventos que
Playwright YA emite (navegaciones de frames, respuestas, peticiones fallidas, errores de pagina, dialogos, descargas, popups, cierres) y solo
conserva CONTADORES y CATEGORIAS:

* clase de host por allowlist del contrato (entry | auth | landing | captcha_provider | other); jamas el host, la URL, la query, el
  fragmento, cabeceras, cookies, cuerpos, tokens ni texto de consola;
* forma de la ruta: solo segmentos cortos y alfabeticos (nombres de ruta del propio portal); cualquier otro segmento es `{x}`;
* tipo de recurso, clase de estado HTTP (2xx/3xx/4xx/5xx) y, para fallos de red, el token `ERR_*` de Chromium;
* tiempos relativos al inicio (ms).

Es un mecanismo de diagnostico: no cambia el comportamiento del flujo y sus fallos se tragan (el diagnostico nunca afecta a la sesion)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Callable
from urllib.parse import urlsplit

MAX_EVENTS = 60
_SEGMENT_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,23}$")
_ERR_TOKEN = re.compile(r"(ERR_[A-Z0-9_]{2,40})")
_CAPTCHA_HINT = re.compile(r"recaptcha|hcaptcha|captcha", re.I)
_ERROR_NAME = re.compile(r"^[A-Za-z]{3,30}$")
# Nombres (JAMAS valores) de parametros de query que se pueden registrar; el resto solo se cuenta.
ALLOWED_QUERY_KEYS = frozenset({"error", "error_description", "code", "state", "session_state", "redirect_uri", "client_id", "scope", "message", "status",
                                "response_type", "nonce", "lang"})
_TITLE_KEYS = (("welcome", re.compile(r"bienvenid", re.I)), ("menu_sol", re.compile(r"men[uú]\s*sol", re.I)), ("login", re.compile(r"iniciar|login|ingres|clave\s*sol", re.I)))


# Segmentos de ruta EXPLICITAMENTE permitidos: nombres de ruta propios del portal (contrato) y palabras genericas de un flujo OAuth/login. Un
# segmento fuera de esta lista NUNCA se registra: se sustituye por una categoria opaca (`{alpha}`, `{num}`, `{alnum}`, `{mixed}`) con un cubo de
# longitud, de modo que un RUC, un nombre, un correo, un codigo o un token incrustado en una ruta no pueda llegar al registro.
ALLOWED_SEGMENTS = frozenset(
    {
        "v1", "v2", "clientessol", "oauth2", "loginMenuSol", "authorize", "token", "callback", "login", "logout", "welcome", "bienvenidos",
        "redirect", "consent", "session", "auth", "authenticate", "menu", "home", "index", "error", "sol.html", "cl-ti-itmenu",
        "MenuInternet.htm", "AutenticaMenuInternet.htm", "SaltoLogin.htm", "j_security_check", "Hang.htm", "aviso.html", "auth_app.js",
        "ol-ti-itmoddatruc", "ol-ti-itconsruc", "api", "static", "js", "css", "img",
    }
)


def _opaque(segment: str) -> str:
    bucket = "<=8" if len(segment) <= 8 else "<=16" if len(segment) <= 16 else ">16"
    if segment.isdigit():
        kind = "num"
    elif segment.isalpha():
        kind = "alpha"
    elif segment.isalnum():
        kind = "alnum"
    else:
        kind = "mixed"
    return "{" + kind + bucket + "}"


def path_shape(path: str) -> list[str]:
    """Forma saneada de una ruta como LISTA (nunca una ruta con barras). SOLO los segmentos de `ALLOWED_SEGMENTS` se conservan; cualquier otro
    se reemplaza por una categoria opaca. Maximo 8 segmentos."""
    parts = []
    for segment in (path or "").split("/")[:12]:
        if not segment:
            continue
        parts.append(segment if segment in ALLOWED_SEGMENTS else _opaque(segment))
    return parts[:8]


_LONG_DIGITS = re.compile(r"\d{6,}")
_MAX_STR = 40


def scrub(node):
    """Defensa en profundidad antes de escribir: cadenas acotadas, sin URL, sin secuencias largas de digitos (RUC/DNI/telefono) ni '@'.
    Lo que no pasa se sustituye por `<redacted>`; claves y listas acotadas."""
    if isinstance(node, dict):
        return {str(k)[:_MAX_STR]: scrub(v) for k, v in list(node.items())[:120]}
    if isinstance(node, (list, tuple)):
        return [scrub(v) for v in list(node)[:80]]
    if isinstance(node, str):
        if len(node) > _MAX_STR or "://" in node or "@" in node or _LONG_DIGITS.search(node):
            return "<redacted>"
        return node
    return node


def status_class(status: int) -> str:
    return f"{status // 100}xx" if 100 <= status < 600 else "other"


class AuthTrace:
    def __init__(self, contract, clock: Callable[[], float], host_of: Callable[[str], tuple]) -> None:
        self._contract = contract
        self._clock = clock
        self._host_of = host_of
        self._t0: float | None = None
        self.events: list[dict] = []
        self.counters: Counter = Counter()
        self.pending_documents = 0  # peticiones de documento (frame principal) iniciadas y aun sin respuesta ni fallo
        self.pending_since: float | None = None
        self.last_document: dict | None = None
        self.crashed: set[int] = set()  # Pages cuyo proceso de renderizado se cerro (evento `crash`)
        self.submitted = False  # la credencial ya se envio: los contadores posteriores se duplican con el prefijo `post:`

    # --- clasificacion saneada ---

    def host_class(self, url: str) -> str:
        try:
            scheme, host, path = self._host_of(url)
        except Exception:  # noqa: BLE001
            return "other"
        c = self._contract
        if host in c.auth_hosts:
            return "auth"
        if host in c.landing_hosts:
            return "landing"
        if host in c.entry_hosts:
            return "entry"
        if _CAPTCHA_HINT.search((host or "") + (path or "")):
            return "captcha_provider"
        return "other"

    def _now_ms(self) -> int:
        now = self._clock()
        if self._t0 is None:
            self._t0 = now
        return int((now - self._t0) * 1000)

    def navigation_pending(self, max_age_seconds: float = 30.0) -> bool:
        """Hay una navegacion del frame principal solicitada y SIN respuesta. En ese estado las lecturas que tocan el documento
        (`title()`, `evaluate`, localizadores) se BLOQUEAN hasta que la navegacion se resuelve: el sondeo debe usar solo datos en cache.
        Un conteo atascado (navegacion cancelada sin evento) deja de contar tras `max_age_seconds`."""
        if self.pending_documents <= 0 or self.pending_since is None:
            return False
        return (self._clock() - self.pending_since) < max_age_seconds

    def _mark_pending(self, delta: int) -> None:
        was = self.pending_documents
        self.pending_documents = max(0, was + delta)
        if self.pending_documents > 0 and was == 0:
            self.pending_since = self._clock()
        elif self.pending_documents == 0:
            self.pending_since = None

    def mark_submitted(self) -> None:
        """A partir de aqui cada contador se duplica con el prefijo `post:`: la deduccion del motivo de un estancamiento usa SOLO lo ocurrido despues del
        envio (un fallo de la pagina publica de entrada, anterior al login, no explica una ventana de autenticacion detenida)."""
        self.submitted = True

    def _bump(self, key: str) -> None:
        self.counters[key] += 1
        if self.submitted:
            self.counters["post:" + key] += 1

    def _event(self, kind: str, **fields) -> None:
        if len(self.events) < MAX_EVENTS:
            self.events.append({"t_ms": self._now_ms(), "e": kind, **fields})

    # --- manejadores (cada uno es a prueba de fallos) ---

    def attach(self, context) -> None:
        context.on("page", self._on_page)

    def _on_page(self, page) -> None:
        try:
            try:
                opener = page.opener() is not None
            except Exception:  # noqa: BLE001
                opener = None
            self.counters["pages_opened"] += 1
            self._event("page_open", has_opener=opener)
            page.on("framenavigated", self._on_navigated)
            page.on("request", self._on_request)
            page.on("response", self._on_response)
            page.on("requestfailed", self._on_request_failed)
            page.on("pageerror", self._on_page_error)
            page.on("console", self._on_console)
            page.on("dialog", self._on_dialog)
            page.on("download", lambda _d: self._count("downloads"))
            page.on("domcontentloaded", lambda _p: self._event("domcontentloaded", host=self.host_class(_p.url)))
            page.on("load", lambda _p: self._event("load", host=self.host_class(_p.url)))
            page.on("close", lambda _p: self._event("page_close"))
            page.on("crash", self._on_crash)
        except Exception:  # noqa: BLE001 -- el diagnostico nunca afecta a la sesion
            pass

    def _on_crash(self, page) -> None:
        try:
            self.crashed.add(id(page))
            self._count("page_crashes")
            self._event("page_crash")
        except Exception:  # noqa: BLE001
            pass

    def _count(self, key: str) -> None:
        self._bump(key)

    def _on_navigated(self, frame) -> None:
        try:
            if frame.parent_frame is not None:
                self._count("subframe_navigations")
                return
            self.pending_documents = 0  # una navegacion se consumo (commit): ninguna peticion anterior sigue pendiente
            self.pending_since = None
            parts = urlsplit(frame.url)
            self._event("navigated", host=self.host_class(frame.url), route=path_shape(parts.path), query=bool(parts.query), fragment=bool(parts.fragment))
        except Exception:  # noqa: BLE001
            pass

    def _on_request(self, request) -> None:
        try:
            if request.resource_type == "document" and request.frame.parent_frame is None:
                self._mark_pending(+1)
                self._event("doc_request", host=self.host_class(request.url), redirect=request.redirected_from is not None)
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, response) -> None:
        try:
            request = response.request
            rtype = request.resource_type
            host = self.host_class(response.url)
            key = f"{rtype}:{status_class(response.status)}:{host}"
            self._bump(key)
            if rtype == "document" and request.frame.parent_frame is None:
                self._mark_pending(-1)
                self.last_document = {"host": host, "status": response.status, "route": path_shape(urlsplit(response.url).path)}
                self._event("doc_response", host=host, status=response.status, route=self.last_document["route"])
            elif response.status >= 400:
                self._event("res_error", type=rtype, status=response.status, host=host)
        except Exception:  # noqa: BLE001
            pass

    def _on_request_failed(self, request) -> None:
        try:
            token = _ERR_TOKEN.search(request.failure or "")
            rtype = request.resource_type
            host = self.host_class(request.url)
            self._bump(f"failed:{rtype}:{host}")
            if rtype == "document" and request.frame.parent_frame is None:
                self._mark_pending(-1)
            self._event("req_failed", type=rtype, host=host, error=token.group(1) if token else "UNKNOWN")
        except Exception:  # noqa: BLE001
            pass

    def _on_page_error(self, error) -> None:
        try:
            name = getattr(error, "name", "") or ""
            self._bump("js_errors")
            self._event("js_error", name=name if _ERROR_NAME.match(name) else "Error")
        except Exception:  # noqa: BLE001
            pass

    def _on_console(self, message) -> None:
        try:
            kind = message.type if message.type in {"error", "warning"} else None
            if kind:
                self._bump(f"console_{kind}")  # SOLO el contador: el texto de consola nunca se lee
        except Exception:  # noqa: BLE001
            pass

    def _on_dialog(self, dialog) -> None:
        try:
            kind = dialog.type if dialog.type in {"alert", "confirm", "prompt", "beforeunload"} else "other"
            self._bump(f"dialog_{kind}")  # el mensaje nunca se lee; Playwright mantiene su comportamiento por defecto
        except Exception:  # noqa: BLE001
            pass

    # --- resumen ---

    def summary(self) -> dict:
        counters = dict(sorted(self.counters.items())[:140])
        return scrub({"events": list(self.events), "counters": counters, "pending_documents": self.pending_documents,
                      "last_document": self.last_document, "submitted": self.submitted})


# Lectura de SOLO LECTURA del estado del documento de la pagina de autenticacion (numeros y booleanos).
DOCUMENT_STATE_JS = r"""
() => {
  const entries = (performance.getEntriesByType && performance.getEntriesByType('resource')) || [];
  const scripts = Array.from(document.scripts || []);
  return {
    ready_state: document.readyState, webdriver_flag: navigator.webdriver === true, has_opener: window.opener !== null,
    title: String(document.title || '').slice(0, 120), pathname: String(location.pathname || '').slice(0, 200),
    query_keys: Array.from(new URLSearchParams(location.search).keys()).slice(0, 16), has_hash: location.hash.length > 1,
    headings: document.querySelectorAll('h1,h2,h3').length, opener_closed: window.opener ? window.opener.closed === true : null,
    history_length: history.length, body_children: document.body ? document.body.children.length : -1,
    scripts_total: scripts.length, scripts_external: scripts.filter(s => s.src).length,
    resources_loaded: entries.length, visible_area: Math.round(Math.min(100, (document.documentElement.scrollHeight * document.documentElement.scrollWidth) / Math.max(1, innerWidth * innerHeight) * 100)),
  };
}
"""


def read_document_state(page) -> dict:
    """Estado del documento de la pagina (lectura pura con un script constante). Nunca lanza."""
    try:
        return sanitize_document_state(page.evaluate(DOCUMENT_STATE_JS))
    except Exception:  # noqa: BLE001 -- pagina en navegacion o cerrada
        return {"ready_state": "unavailable"}


def sanitize_document_state(raw: dict) -> dict:
    def num(key):
        value = raw.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else -1

    ready = raw.get("ready_state")
    title = str(raw.get("title") or "")
    title_key = "empty" if not title.strip() else next((key for key, rx in _TITLE_KEYS if rx.search(title)), "other")
    keys = [k for k in (raw.get("query_keys") or []) if isinstance(k, str)]
    return {
        "title_key": title_key, "route": path_shape(str(raw.get("pathname") or "")),
        "query_keys": sorted({k for k in keys if k in ALLOWED_QUERY_KEYS}), "query_other_keys": len([k for k in keys if k not in ALLOWED_QUERY_KEYS]),
        "has_hash": bool(raw.get("has_hash")), "headings": num("headings"),
        "opener_closed": raw.get("opener_closed") if isinstance(raw.get("opener_closed"), bool) else None,
        **_document_core(raw, ready, num),
    }


def _document_core(raw: dict, ready, num) -> dict:
    return {
        "ready_state": ready if ready in {"loading", "interactive", "complete"} else "unknown",
        "webdriver_flag": bool(raw.get("webdriver_flag")), "has_opener": bool(raw.get("has_opener")),
        "history_length": num("history_length"), "body_children": num("body_children"), "scripts_total": num("scripts_total"),
        "scripts_external": num("scripts_external"), "resources_loaded": num("resources_loaded"), "visible_area_pct": num("visible_area"),
    }


def stall_reason(summary: dict, document: dict | None) -> str:
    """Motivo de una autenticacion detenida, deducido SOLO de la traza (nunca se afirma que SUNAT fallo sin evidencia)."""
    counters = summary.get("counters", {})
    if summary.get("submitted"):
        # SOLO lo ocurrido despues del envio de la credencial (la pagina publica de entrada y el login previo no explican la ventana detenida)
        counters = {key[5:]: value for key, value in counters.items() if key.startswith("post:")}
    last = summary.get("last_document") or {}
    if (last.get("status") or 0) >= 500:
        return "SERVER_ERROR_DOCUMENT"
    if summary.get("pending_documents", 0) > 0 or (document or {}).get("ready_state") in {"loading", "pending_navigation"}:
        return "NAVIGATION_PENDING_NO_RESPONSE"
    # un recurso con respuesta HTTP de error tambien aparece como `requestfailed` (ERR_ABORTED): la respuesta HTTP es la evidencia mas precisa
    if any(k.startswith(("script:4xx", "script:5xx", "xhr:4xx", "xhr:5xx", "fetch:4xx", "fetch:5xx")) for k in counters):
        return "RESOURCE_HTTP_ERROR"
    if any(k.startswith(("failed:script", "failed:xhr", "failed:fetch")) for k in counters):
        return "RESOURCE_LOAD_FAILED"
    if counters.get("js_errors", 0) > 0:
        return "SCRIPT_ERROR"
    if counters.get("dialog_alert", 0) + counters.get("dialog_confirm", 0) + counters.get("dialog_beforeunload", 0) > 0:
        return "DIALOG_DISMISSED_BY_BROWSER"
    if (document or {}).get("ready_state") == "complete":
        return "STABLE_DOCUMENT_NO_CONTINUATION"
    return "UNDETERMINED"
