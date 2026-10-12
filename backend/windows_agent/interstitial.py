"""M2 -- clasificador ESTRUCTURAL y de SOLO LECTURA de pantallas intermedias del portal SOL (avisos, modales, validacion de datos de contacto).

Se usa unicamente cuando un clic de navegacion no es accionable porque algo cubre el menu. NUNCA se pulsa nada en estas pantallas (ni "Finalizar", ni
enviar codigos, ni confirmar/actualizar contacto): se clasifica, se informa "Requiere tu intervencion", se mantiene el navegador abierto y se espera al
humano. Despues de su intervencion se REVALIDA el shell antes de continuar.

Minimizacion estricta: del navegador solo salen etiquetas de un vocabulario CERRADO de palabras genericas, estructura (rol, clases tecnicas), booleanos
y contadores. Ningun telefono, correo, codigo ni otro dato personal; lo que no pertenece al vocabulario se descarta en el propio agente.

Categorias (las de shell autenticado y Ficha las resuelven `destination_contract` y el extractor):
  CONTACT_VALIDATION_INTERSTITIAL  pantalla/aviso de validacion de datos de contacto
  INFORMATIONAL_MODAL              ventana informativa superpuesta
  HUMAN_ACTION_REQUIRED            superposicion bloqueante no reconocida: requiere al humano
  UNKNOWN_PORTAL_STATE             sin superposicion visible reconocible"""

from __future__ import annotations

import re

CONTACT_VALIDATION_INTERSTITIAL = "CONTACT_VALIDATION_INTERSTITIAL"
INFORMATIONAL_MODAL = "INFORMATIONAL_MODAL"
HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"
UNKNOWN_PORTAL_STATE = "UNKNOWN_PORTAL_STATE"

# Categorias que se reportan como MOTIVO de la espera humana (lista cerrada replicada en el backend).
WAITING_REASONS = frozenset({CONTACT_VALIDATION_INTERSTITIAL, INFORMATIONAL_MODAL, HUMAN_ACTION_REQUIRED})

VOCABULARY = frozenset(
    """
    informativo informativa aviso avisos mensaje importante finalizar finaliza continuar siguiente omitir mas tarde recordar despues cerrar aceptar cancelar
    confirmar actualizar guardar enviar envio codigo codigos verificacion verifica verificar validacion valida valide validar tus tu sus su datos dato contacto
    correo electronico telefono movil celular numero de del la el los las en y o para con por que se si no sunat sol clave mi mis ruc
    """.split()
)
_WORD = re.compile(r"[a-z0-9]+")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

INTERSTITIAL_JS = r"""
() => {
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const words = (s) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
  const vw = Math.max(window.innerWidth, 1), vh = Math.max(window.innerHeight, 1);
  const dialogs = [];
  const sel = '[role=dialog],[role=alertdialog],[aria-modal=true],.modal,.modal-dialog,.ui-dialog,.swal2-popup,[class*=popup],[class*=overlay],[class*=modal]';
  for (const el of document.querySelectorAll(sel)) {
    if (dialogs.length >= 6) break;
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    const buttons = Array.from(el.querySelectorAll('button,input[type=button],input[type=submit],a.btn,[role=button]')).filter(visible).slice(0, 8)
      .map(b => ({ role: b.getAttribute('role'), type: b.getAttribute('type'), text: words(b.innerText || b.value || b.getAttribute('aria-label')) }));
    const heads = Array.from(el.querySelectorAll('h1,h2,h3,h4,h5,legend,.modal-title,[class*=title]')).filter(visible).slice(0, 4).map(h => words(h.innerText));
    dialogs.push({ tag: el.tagName, role: el.getAttribute('role'), aria_modal: el.getAttribute('aria-modal') === 'true',
      classes: Array.from(el.classList).slice(0, 4), fixed: st.position === 'fixed',
      coverage: Math.min(100, Math.round((r.width * r.height) / (vw * vh) * 100)), buttons, heads, text: words(el.innerText) });
  }
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,legend,.titulo,[class*=title]')).filter(visible).slice(0, 10).map(h => words(h.innerText));
  const controls = Array.from(document.querySelectorAll('button,input[type=button],input[type=submit],[role=button]')).filter(visible).slice(0, 24)
    .map(b => words(b.innerText || b.value || b.getAttribute('aria-label')));
  return { dialogs, headings, controls };
}
"""


def vocab_words(text: str) -> list[str]:
    """Palabras del texto que pertenecen al vocabulario cerrado (el resto se DESCARTA; nunca sale texto libre)."""
    return [w for w in _WORD.findall((text or "").lower()) if w in VOCABULARY][:12]


def sanitize(raw: dict) -> dict:
    """Reduce la lectura cruda a estructura + palabras del vocabulario + contadores. Es lo unico que se registra/transmite."""
    dialogs = []
    for d in (raw.get("dialogs") or [])[:6]:
        classes = [c for c in (d.get("classes") or [])[:4] if isinstance(c, str) and _SAFE_TOKEN.match(c)]
        role = d.get("role")
        dialogs.append(
            {
                "tag": str(d.get("tag", ""))[:8] if re.fullmatch(r"[A-Za-z0-9]{1,8}", str(d.get("tag", ""))) else "?",
                "role": role if isinstance(role, str) and _SAFE_TOKEN.match(role) else None,
                "aria_modal": bool(d.get("aria_modal")),
                "classes": classes,
                "fixed": bool(d.get("fixed")),
                "coverage_pct": int(d.get("coverage", 0)),
                "button_count": len(d.get("buttons") or []),
                "button_words": [vocab_words(b.get("text", "")) for b in (d.get("buttons") or [])[:8]],
                "heading_words": [vocab_words(h) for h in (d.get("heads") or [])[:4]],
                "text_words": vocab_words(d.get("text", "")),
            }
        )
    return {
        "dialogs": dialogs,
        "heading_words": [vocab_words(h) for h in (raw.get("headings") or [])[:10]],
        "control_words": [vocab_words(c) for c in (raw.get("controls") or [])[:24]],
    }


def _tokens(observation: dict) -> set[str]:
    out: set[str] = set()
    for dialog in observation.get("dialogs", []):
        out.update(dialog.get("text_words", []))
        for group in dialog.get("heading_words", []) + dialog.get("button_words", []):
            out.update(group)
    for group in observation.get("heading_words", []):
        out.update(group)
    return out


def classify(observation: dict) -> str:
    """Categoria de la pantalla intermedia a partir de la lectura SANEADA (nunca de la cruda)."""
    tokens = _tokens(observation)
    if {"datos", "contacto"} <= tokens and tokens & {"valida", "valide", "validar", "validacion", "verifica", "verificar", "verificacion"}:
        return CONTACT_VALIDATION_INTERSTITIAL
    dialogs = observation.get("dialogs", [])
    if any("informativo" in d.get("text_words", []) or any("informativo" in h for h in d.get("heading_words", [])) for d in dialogs):
        return INFORMATIONAL_MODAL
    if any(d.get("coverage_pct", 0) >= 40 or d.get("aria_modal") for d in dialogs):
        return HUMAN_ACTION_REQUIRED
    return UNKNOWN_PORTAL_STATE
