"""Soporte del harness E3-LIVE-B (solo aceptacion; no es codigo de producto).

`LiveExecutor` ENVUELVE al ejecutor de producto sin cambiar su comportamiento: solo (1) crea el grant de entrega
justo DESPUES de que el portal quedo validado y ANTES de que el agente pida la credencial (asi, si la validacion
pre-auth falla, nunca existe un grant, y el grant de 60 s no caduca por la latencia de la red), y (2) captura
evidencia SANITIZADA (hosts, rutas sin identificadores, titulo, hosts de frames, booleanos de identidad) usando las
mismas vistas de Page/Frame que el producto -- nunca HTML, DOM, cookies ni valores de campos."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from windows_agent.browser_executor import SupervisedAuthExecutor
from windows_agent.portal_identity import is_accepted_landing

_ID_SEGMENT = re.compile(r"^(?:[0-9a-fA-F-]{16,}|[A-Za-z0-9_-]{24,})$")


# Unicos titulos que pueden aparecer en el resultado (literales esperados del portal; Spike A / LIVE-A1). Cualquier otro
# titulo NO se escribe: solo `title_match: false`.
ALLOWED_TITLES = frozenset({"SUNAT - Menú SOL", "SUNAT SOL Operaciones en Línea"})
_SAFE_HOST = re.compile(r"^[a-z0-9.-]{1,100}$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]{1,60}$")


def _safe_host(host: str | None) -> str:
    host = (host or "").lower()
    return host if _SAFE_HOST.match(host) else "<unexpected-host>"


def _safe_path(path: str) -> str:
    """Ruta sin identificadores: segmentos tipo id -> <id>; segmentos con caracteres no esperados -> <seg>."""
    out = []
    for seg in path.split("/"):
        if not seg:
            continue
        if _ID_SEGMENT.match(seg):
            out.append("<id>")
        elif _SAFE_SEGMENT.match(seg):
            out.append(seg)
        else:
            out.append("<seg>")
    return "/" + "/".join(out)


_SAFE_DETAIL = re.compile(r"^[A-Za-z0-9 '_.,-]{1,120}$")


def _safe_detail(detail: str | None) -> str | None:
    """`detail` lo genera el CODIGO del producto (nunca texto de la pagina); aun asi se acota por patron."""
    return detail if detail is None or _SAFE_DETAIL.match(detail) else "<unexpected-detail>"


class LiveExecutor(SupervisedAuthExecutor):
    def __init__(self, contract, *, evidence: dict, authorize_hook, **kwargs) -> None:
        super().__init__(contract, **kwargs)
        self._evidence = evidence
        self._authorize_hook = authorize_hook

    def _capture(self) -> dict:
        try:
            pages = []
            for page in self._pages():
                view = self._view(page)
                parts = urlsplit(view.url)
                pages.append({
                    "scheme": parts.scheme if parts.scheme in {"http", "https"} else "<other>",
                    "host": _safe_host(parts.hostname), "path": _safe_path(parts.path),
                    "title_match": (view.title or "") in ALLOWED_TITLES,
                    **({"title": view.title} if (view.title or "") in ALLOWED_TITLES else {}),
                    "frame_hosts": sorted({_safe_host(urlsplit(u).hostname) for u in view.frame_urls}),
                    "password_field_visible": view.password_field_visible,
                    "matches_landing_contract": is_accepted_landing(view, self._contract),
                })
            return {"pages": pages, "top_level_hosts": sorted({p["host"] for p in pages})}
        except Exception as exc:  # noqa: BLE001 -- solo el tipo
            return {"capture_error": type(exc).__name__}

    def open_and_identify_login(self):
        result = super().open_and_identify_login()
        self._evidence["mode_activation_clicks"] = self.mode_activation_clicks
        if result is None:
            self._evidence["preauth_contract_match"] = True
            self._evidence["preauth_surface"] = self._capture()
            self._authorize_hook()  # grant SOLO tras validar el portal; la entrega sigue siendo de un solo uso
        else:
            self._evidence["preauth_contract_match"] = False
            self._evidence["preauth_failure"] = {
                "outcome": result.outcome.value, "detail": _safe_detail(result.detail),
                "terminal_reason_code": result.terminal_reason_code,
            }
            self._evidence["preauth_surface"] = self._capture()
        return result

    def await_outcome(self):
        result = super().await_outcome()
        self._evidence["post_submit_surface"] = self._capture()
        self._evidence["mode_activation_clicks"] = self.mode_activation_clicks
        return result
