"""M3 v2 -- reconocimiento GUIADO por checkpoints PRE (menu expandido, antes del clic) y LLEGADA (despues del clic manual). SOLO OBSERVA.

Arranque (un solo comando, desde la raiz del repositorio):
    .\\scripts\\m3_recon.ps1

Garantias (verificadas por pruebas y por la auditoria estatica `m3_recon_selftest.py`):
* CERO clics y CERO teclado automaticos, CERO relleno de campos, CERO credenciales/cookies/tokens/portapapeles. El propietario inicia sesion y hace
  TODOS los clics. La herramienta no hace `click`, `fill`, `type`, `press`, `goto` sobre SUNAT ni `evaluate` con efectos.
* Registro ESTRUCTURAL saneado: sin HTML, sin capturas, sin valores de campos, sin query/fragmento, sin RUC ni nombres. Del texto de menu solo se
  guarda una clave canonica (lista blanca) o una HUELLA de 12 letras; de la red solo metodo + ruta normalizada (nunca cuerpos ni cabeceras).
* Archivo incremental con escritura atomica: se puede cerrar y reanudar sin perder checkpoints; repetir uno marca el anterior SUPERSEDED.
* Salida FUERA del repositorio. No commitear."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from datetime import date
from urllib.parse import urlsplit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m2_recon_sanitizer as san  # noqa: E402
import m2_sol_recon as v1  # noqa: E402
import m3_chromium_launch as chromium  # noqa: E402
import m3_recon_core as core  # noqa: E402

ENTRY_URL = v1.ENTRY_URL
RC_OK, RC_TIMEOUT, RC_BAD_ARGS, RC_BROWSER_CLOSED = 0, 1, 2, 3

# Extractor de SOLO LECTURA (constante). Devuelve estructura de menu (jerarquia, visibilidad, relacion con el accionable), la vista generica v1 y
# contadores. El texto NO sale del navegador salvo `text_probe` cuando es IGUAL a una forma de la lista blanca; de las hojas de MENU sale ademas una
# huella FNV-1a (12 letras). No lee valores de campos, cookies ni storage.
_EXTRACT_V2_JS = r"""
(args) => {
  const [navAllow, routeAllow] = args;
  const allowedNav = new Set(navAllow), allowedRoute = new Set(routeAllow);
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const chainVisible = (el) => { for (let p = el; p && p.nodeType === 1; p = p.parentElement) { const st = getComputedStyle(p); if (st.display === 'none' || st.visibility === 'hidden') return false; } return true; };
  const fnv = (text) => { let h = 0xcbf29ce484222325n; const M = 0xffffffffffffffffn; const s = norm(text).slice(0, 120);
    for (let i = 0; i < s.length; i++) { h = ((h ^ BigInt(s.charCodeAt(i))) * 0x100000001b3n) & M; }
    let out = ''; for (let i = 0; i < 12; i++) out += String.fromCharCode(103 + Number((h >> BigInt(60 - 4 * i)) & 0xfn)); return out; };
  const structural = new Set(['a','button','input','select','textarea','form','nav','ul','ol','li','table','iframe','frame','h1','h2','h3','h4','section','header','footer','main','aside']);
  const nodes = []; let scanned = 0;
  for (const el of document.querySelectorAll('*')) {
    if (++scanned > 6000 || nodes.length >= 800) break;
    const tag = el.tagName.toLowerCase();
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.nodeValue).join(' ');
    const t = norm(own), a = norm(el.getAttribute('aria-label'));
    const textProbe = allowedNav.has(t) ? t : null, ariaProbe = allowedNav.has(a) ? a : null;
    if (!(structural.has(tag) || el.id || el.getAttribute('role') || textProbe || ariaProbe)) continue;
    let depth = 0; for (let p = el; p.parentElement; p = p.parentElement) depth++;
    const par = el.parentElement;
    nodes.push({ tag, id: el.id || null, classes: (typeof el.className === 'string') ? el.className.split(/\s+/).filter(Boolean) : [], role: el.getAttribute('role'),
      type: tag === 'input' ? (el.getAttribute('type') || 'text') : null, text_probe: textProbe, aria_probe: ariaProbe,
      href: (tag === 'a' && textProbe) ? el.getAttribute('href') : null, visible: vis(el), depth,
      sibling_index: par ? Array.prototype.indexOf.call(par.children, el) : 0,
      parent_tag: par ? par.tagName.toLowerCase() : null, parent_id: par ? (par.id || null) : null, has_onclick: el.hasAttribute('onclick'),
      ancestor_cuerpos: (() => { let n = 0; for (let p = el.parentElement; p; p = p.parentElement) { if (p.tagName === 'LI' && p.classList.contains('liCuerpo')) n++; } return n; })() });
  }
  // --- MENU: hojas y cabeceras `li.nivelN > span.spanNivelDescripcion` con jerarquia, visibilidad y relacion con el accionable ---
  const menu = []; let menuTruncated = false; const seen = new Map();
  for (const span of document.querySelectorAll('li span.spanNivelDescripcion')) {
    if (menu.length >= 1500) { menuTruncated = true; break; }
    const li = span.closest('li'); if (!li) continue;
    const m = Array.from(li.classList).map(c => /^nivel(\d)$/.exec(c)).find(Boolean); if (!m) continue;
    const text = norm(span.textContent);
    const ancestors = []; let cuerpos = 0;
    for (let p = li.parentElement; p; p = p.parentElement) { if (p.tagName === 'LI' && p.classList.contains('liCuerpo')) { cuerpos++; ancestors.unshift((p.id || '').replace('Cuerpo', '')); } }
    let container = null; for (let p = li.parentElement; p; p = p.parentElement) { if (p.id && !/^nivel/.test(p.id)) { container = p.id; break; } }
    const next = li.nextElementSibling;
    const anchor = span.closest('a') || li.querySelector(':scope > a');
    const ptr = (el) => { try { return getComputedStyle(el).cursor === 'pointer'; } catch (e) { return false; } };
    const hasClickAttr = li.hasAttribute('onclick') || span.hasAttribute('onclick');
    // El menu real de SOL no usa <a> ni onclick: el clic se enlaza por script. Una afordancia PASIVA valida es `cursor: pointer` (lo que ve el usuario).
    const act = anchor || (hasClickAttr ? (li.hasAttribute('onclick') ? li : span) : (ptr(span) ? span : (ptr(li) ? li : null)));
    let kind = 'none';
    if (act) { if (act.tagName === 'A') { const h = (act.getAttribute('href') || '').trim().toLowerCase(); kind = (h.startsWith('javascript:') || h === '#' || h === '') ? 'script' : 'path'; } else kind = hasClickAttr ? 'onclick' : 'pointer'; }
    const key = (li.id || '') + '|' + text; const idx = seen.get(key) || 0; seen.set(key, idx + 1);
    menu.push({ li_id: li.id || null, level: Number(m[1]), is_leaf: !(next && next.classList.contains('liCuerpo')), text_probe: allowedRoute.has(text) ? text : null,
      digest: text ? fnv(text) : null, visible: vis(span), chain_visible: chainVisible(span), ancestors: ancestors.slice(0, 8), cuerpos, dup_index: idx, container_id: container,
      actionable: { tag: act ? act.tagName.toLowerCase() : null, id: act ? (act.id || null) : null, kind, visible: act ? vis(act) : false } });
  }
  for (const n of menu) n.dup_count = seen.get((n.li_id || '') + '|' + '') || undefined;
  // --- PAGINA PUBLICA: tarjetas de entrada (encabezado + control INGRESAR). Solo estructura; el texto del encabezado sale como clave canonica o huella ---
  const cards = [];
  const isIng = (el) => norm(el.textContent) === 'ingresar' && !Array.from(el.children).some((c) => norm(c.textContent) === 'ingresar');
  const ingList = Array.from(document.querySelectorAll('a,button,span')).filter(isIng);
  const ingSet = new Set(ingList);
  ingList.forEach((el, index) => {
    if (cards.length >= 12) return;
    let box = el, heads = [];
    for (let up = 0; up < 8 && box.parentElement; up++) {
      box = box.parentElement;
      const inside = ingList.filter((x) => box.contains(x));
      if (inside.length !== 1) break;
      heads = Array.from(box.querySelectorAll('h1,h2,h3,h4')).slice(0, 3);
      if (heads.length) break;
    }
    if (!heads.length) return;
    const chain = []; for (let p = el.parentElement, i = 0; p && i < 5; p = p.parentElement, i++) chain.push({ tag: p.tagName.toLowerCase(), id: p.id || null, classes: (typeof p.className === 'string') ? p.className.split(/\s+/).filter(Boolean) : [] });
    cards.push({ index, visible: vis(el),
      headings: heads.map((h) => { const t = norm(h.textContent); return { tag: h.tagName.toLowerCase(), text_probe: allowedRoute.has(t) ? t : (allowedNav.has(t) ? t : null), digest: t ? fnv(t) : null, visible: vis(h) }; }),
      ingresar: { tag: el.tagName.toLowerCase(), id: el.id || null, classes: (typeof el.className === 'string') ? el.className.split(/\s+/).filter(Boolean) : [], visible: vis(el), parent_tag: el.parentElement ? el.parentElement.tagName.toLowerCase() : null },
      chain });
  });
  const counts = { file_inputs: document.querySelectorAll('input[type=file]').length, forms: document.forms.length,
    tables_visible: Array.from(document.querySelectorAll('table')).filter(vis).length, iframes: document.querySelectorAll('iframe,frame').length,
    password_controls_visible: Array.from(document.querySelectorAll('input[type=password]')).filter(vis).length,
    buttons_visible: Array.from(document.querySelectorAll('button')).filter(vis).length };
  return { title: document.title, nodes, truncated: scanned > 6000 || nodes.length >= 800, menu_nodes: menu, menu_truncated: menuTruncated, counts, cards,
           focused: document.hasFocus(), visibility: document.visibilityState };
}
"""


def _fix_dup_counts(view: dict) -> dict:
    """Completa `dup_count`: cuantos nodos comparten (li_id, huella) -> detecta 'elementos repetidos dentro de un li'."""
    groups: dict[tuple, int] = {}
    for n in view.get("menu_nodes", []):
        groups[(n["li_id"], n["digest"])] = groups.get((n["li_id"], n["digest"]), 0) + 1
    for n in view.get("menu_nodes", []):
        n["dup_count"] = groups[(n["li_id"], n["digest"])]
    return view


async def _frame_view_v2(frame, timeout: float) -> dict:
    if frame.is_detached():
        return {"error": True}
    try:
        raw = await asyncio.wait_for(frame.evaluate(_EXTRACT_V2_JS, [san.NAV_TEXT_FORMS, san.ROUTE_TEXT_FORMS]), timeout)
        return _fix_dup_counts(san.sanitize_view_v2(raw)) if not san.sanitize_view_v2(raw).get("error") else {"error": True}
    except Exception:  # noqa: BLE001 -- frame en navegacion/inaccesible/timeout: no debe tumbar el reconocimiento
        return {"error": True}


class NetworkWatcher:
    """Registro PASIVO de peticiones (solo metodo + URL saneada + tipo). No lee cuerpos ni cabeceras; no intercepta ni modifica nada."""

    def __init__(self, context) -> None:
        self.items: list[dict] = []
        context.on("request", lambda r: self.items.append({"method": r.method, "url": r.url, "resource_type": r.resource_type}))

    def reset(self) -> None:
        self.items = []

    def snapshot(self) -> list[dict]:
        """Todo lo visto desde el ultimo PRE, SIN vaciar: repetir la LLEGADA no pierde el trafico de los intentos anteriores."""
        return list(self.items)


async def capture(context, registry: core.Registry, watcher: NetworkWatcher, *, destination: str, kind: str, store: core.Store, attempt: int,
                  linked_pre: str | None = None, timeout: float = 10.0) -> dict:
    """Relee TODAS las Pages y frames vivos AHORA. Cero interaccion con la pagina."""
    windows = []
    for page in v1.live_pages(context):
        try:
            opener = await page.opener()
        except Exception:  # noqa: BLE001
            opener = None
        win = {"window_id": registry.window(page), "opener": registry.window(opener) if opener is not None else None,
               "url": san.safe_url(page.url), "frames": [], "active": False}
        for frame in list(page.frames):
            view = await _frame_view_v2(frame, timeout)
            if frame == page.main_frame and "focused" in view:
                win["active"] = bool(view.get("focused"))
            win["frames"].append({"frame_id": registry.frame(frame), "is_main": frame == page.main_frame, "url": san.safe_url(frame.url), "view": view})
        windows.append(win)
    cp = {"record": "checkpoint", "checkpoint_id": store.next_id(), "destination": destination, "kind": kind, "attempt": attempt, "status": "ACTIVE",
          "date": date.today().isoformat(), "windows": windows, "linked_pre": linked_pre, "same_journey_confirmed": None, "observed": None}
    if kind == "ARRIVAL":
        cp["network"] = san.sanitize_network(watcher.snapshot())
    elif kind == "PRE":
        watcher.reset()  # el periodo de red que importa empieza en el PRE (V/«PEEK» no lo reinicia)
    return cp


# ------------------------------------------------------------------------------------------------------------------ interfaz guiada
MARK = {"COMPLETE": "OK ", "PARTIAL": "~  ", "MISSING": "-  ", "PRIOR_ONLY": "v1", "CONFIRMED": "OK ", "UNCONFIRMED": "?  ", "NOT_CAPTURED": "-  "}


def render_matrix(mx: dict, selected: str | None = None) -> str:
    lines = ["", "  EVIDENCIAS (OK completa · ~ parcial · - falta · v1 = llegada previa suficiente sin vínculo)", "  #  Destino                                         PRE  LLEGADA  VÍNCULO  CONJUNTO"]
    for i, key in enumerate(core.ORDER, 1):
        r = mx["destinations"][key]
        lines.append(f"  {i}{'*' if key == selected else ' '} {core.SPECS[key].title:<46} {MARK[r['pre']]}   {MARK.get(r['arrival'], r['arrival']):<6}   {MARK[r['link']]}    {r['overall']}")
    for key, why in mx["resolved_elsewhere"].items():
        lines.append(f"  = {key}: {why}")
    return "\n".join(lines)


def portal_frames(cp: dict) -> list[tuple[str, str, str, int, int]]:
    """(ventana, frame, host+ruta, nodos de menu, hojas de ruta visibles) de los frames del portal de un checkpoint: solo identificadores tecnicos saneados."""
    out = []
    for w, f in core.iter_frames(cp):
        if core._is_noise(f) or not core.host_is_portal(f["url"].get("host")):
            continue
        menu = core._menu_nodes(f)
        cards = (f.get("view") or {}).get("cards") or []
        out.append((w["window_id"], f["frame_id"], f"{f['url'].get('host')}{f['url'].get('path') or ''}"[:70], len(menu) + len(cards),
                    sum(1 for n in menu if n.get("label") and n["visible"] and n["chain_visible"]) + sum(1 for c in cards if c["visible"] and any(h.get("label") for h in c["headings"]))))
    return out


async def run_guided(context, cfg: v1.ReconConfig, store: core.Store, prior: dict, read_line, say=print, public_only: bool = False) -> str:
    """Devuelve KEEP (terminar conservando el navegador), CLOSE (cerrarlo) o el codigo de salida de error como texto RC:<n>."""
    registry, watcher = core.Registry(), NetworkWatcher(context)
    v1._page_events(context, say)
    loop = asyncio.get_running_loop()
    if public_only:
        say("MODO PÚBLICO: no hace falta iniciar sesión. Solo se observa la página pública de SUNAT (elija el destino 4 y escriba P).")
    elif not [p for p in v1.live_pages(context) if v1.classify_pages([p.url], cfg)["candidates"]]:
        say(f"{v1.ST_WAIT_LOGIN}: en Chromium haga clic en 'Ingresar' e inicie sesión usted mismo. Esta herramienta NO escribe ni guarda credenciales.")
    started, authenticated, last = loop.time(), public_only, loop.time()
    ever = bool(v1.live_pages(context))
    while not authenticated and loop.time() - started < cfg.login_timeout_s:
        pages = v1.live_pages(context)
        ever = ever or bool(pages)
        if not pages and ever:
            say(f"{v1.ST_BROWSER_CLOSED}: se cerraron todas las ventanas. Lo registrado hasta ahora se conserva.")
            return f"RC:{RC_BROWSER_CLOSED}"
        if v1.classify_pages([p.url for p in pages], cfg)["candidates"]:
            authenticated = True
            break
        if loop.time() - last >= cfg.status_every_s:
            say(f"{v1.ST_WAIT_LOGIN}…")
            last = loop.time()
        await asyncio.sleep(cfg.poll_s)
    if not authenticated:
        say(f"{v1.ST_TIMEOUT}: no se detectó una sesión autenticada.")
        return f"RC:{RC_TIMEOUT}"
    reused = store.derive_shared_pre()
    if reused:
        say(f"   Reutilizados PRE ya capturados (evidencia real previa) para: {', '.join(reused)}")
    say(f"{v1.ST_AUTHENTICATED}. Comandos: número = elegir destino · P = registrar MENÚ EXPANDIDO (antes del clic) · L = registrar LLEGADA (después de su clic)")
    say("   V = ver ventanas/frames · V <ventana> <frame> = fijar dónde está el menú (o la llegada) · K <n> = posición de la tarjeta pública · R = repetir lo defectuoso · M = matriz")
    say("   Q = terminar CONSERVANDO el navegador (puede continuar sin volver a autenticarse) · X = terminar y CERRAR el navegador. NO haga clic con la herramienta: los clics son suyos.")
    selected = next((k for k in core.ORDER if matrix_row(store, prior, k)["overall"] != "COMPLETE"), core.ORDER[0])
    say(render_matrix(core.matrix(store, prior), selected))
    say(f"\n  Destino actual: {core.SPECS[selected].title}\n  1) {core.SPECS[selected].instructions_pre}\n  2) Escriba P.")
    pending_arrival: dict | None = None
    context_sel: dict | None = None  # ventana/frame fijado con V para el PRE
    card_choice: int | None = None  # posicion de la tarjeta publica fijada con K (solo destinos de entrada publica)
    while True:
        cmd = ((await read_line()) or "Q").strip().upper()
        if not v1.live_pages(context) and cmd not in {"Q", "X"}:
            say(f"{v1.ST_BROWSER_CLOSED}: se conserva lo registrado."); return f"RC:{RC_BROWSER_CLOSED}"
        spec = core.SPECS[selected]
        if cmd in {"Q", "X"}:
            say("Listo. Registro conservado; ejecute el mismo comando para continuar." + (" Navegador CONSERVADO." if cmd == "Q" else " Navegador cerrado."))
            return "KEEP" if cmd == "Q" else "CLOSE"
        if cmd == "M":
            say(render_matrix(core.matrix(store, prior), selected)); continue
        if cmd.isdigit() and 1 <= int(cmd) <= len(core.ORDER):
            selected = core.ORDER[int(cmd) - 1]; spec = core.SPECS[selected]; context_sel = None
            say(f"\n  Destino: {spec.title}\n  1) {spec.instructions_pre}\n  2) Escriba P.")
            continue
        if cmd.split()[:1] == ["V"]:
            parts = cmd.split()
            if len(parts) == 3 and pending_arrival is not None:  # elegir la LLEGADA entre varias candidatas
                pending_arrival["observed"] = {"window": parts[1], "frame": parts[2]}
                say(await _finish_arrival(store, pending_arrival, spec, store.pre_for(selected), read_line, say)); pending_arrival = None
                continue
            if len(parts) == 3:  # fijar el contexto del PRE
                context_sel = {"window": parts[1], "frame": parts[2]}
                say(f"   Contexto fijado: {parts[1]}/{parts[2]}. Escriba P para registrar el menú SOLO de ese frame. (V sin argumentos lo lista; «V 0» lo libera)")
                continue
            if len(parts) == 2 and parts[1] == "0":
                context_sel = None
                say("   Contexto liberado: P observará todas las ventanas y frames."); continue
            snap = await capture(context, registry, watcher, destination=selected, kind="PEEK", store=store, attempt=0)
            say("   Ventanas/frames del portal (identificadores técnicos · host+ruta · nodos de menú · hojas de ruta visibles):")
            for w, f, hp, n, vis in portal_frames(snap):
                say(f"     {w}/{f}  {hp}  menú={n}  visibles={vis}")
            say("   Fije con «V <ventana> <frame>» el frame donde está el menú expandido y luego escriba P."); continue
        if cmd.split()[:1] == ["K"]:
            parts = cmd.split()
            if len(parts) == 2 and parts[1].isdigit():
                card_choice = int(parts[1])
                say(f"   Tarjeta pública fijada en la posición {card_choice}. Escriba P para registrarla. (K 0 no es «ninguna»: use «K -» para quitar la elección)")
            elif len(parts) == 2 and parts[1] == "-":
                card_choice = None
                say("   Elección de tarjeta liberada: P buscará el encabezado «Mis Declaraciones y Pagos».")
            else:
                say("   Use «K <n>» con la posición (0..n-1, de arriba abajo) de la tarjeta «Mis Declaraciones y Pagos».")
            continue
        if cmd == "R":
            pre_cp, arr_cp = store.pre_for(selected), store.active(selected, "ARRIVAL")
            if pre_cp is None or core.evaluate_pre(spec, pre_cp)["status"] != "COMPLETE":
                cmd = "P"
            elif arr_cp is None or core.evaluate_arrival(spec, pre_cp, arr_cp)["status"] != "COMPLETE":
                cmd = "L"
            else:
                say("   Nada defectuoso en este destino: PRE y LLEGADA están completos."); continue
        if cmd == "P":
            attempt = 1 + sum(1 for c in store.checkpoints if c["destination"] == selected and c["kind"] == "PRE")
            cp = await capture(context, registry, watcher, destination=selected, kind="PRE", store=store, attempt=attempt)
            if context_sel:
                cp["observed"] = dict(context_sel)
            if card_choice is not None and spec.entry_kind == "PUBLIC_CARD":
                cp["card_choice"] = card_choice
            ev = core.evaluate_pre(spec, cp)
            store.add(cp)  # se conserva aunque sea parcial: cuenta como evidencia PARCIAL y el anterior queda SUPERSEDED
            if ev["status"] == "COMPLETE":
                e = ev["entry"]
                if e["kind"] == "PUBLIC_CARD":
                    say(f"   [PRE {cp['checkpoint_id']} registrado en {e['window']}/{e['frame']}: tarjeta pública «{e['label']}» con su INGRESAR visible (card {e['card_index']}, {e['ingresar_tag']})]")
                else:
                    say(f"   [PRE {cp['checkpoint_id']} registrado en {e['window']}/{e['frame']}: hoja visible, ruta {' → '.join(e['ancestor_labels'] + [e['label']])}, objetivo={e['click_target']}]")
                for note in e.get("notes", []):
                    say(f"   nota: {note}")
                more = store.derive_shared_pre()
                if more:
                    say(f"   Esta misma captura también evidencia la hoja visible de: {', '.join(more)} (registrado como PRE reutilizado).")
                say(f"   3) {spec.instructions_arrival}")
                say("   4) Cuando termine de cargar, escriba L.")
            else:
                say(f"   [PRE {cp['checkpoint_id']} registrado como PARCIAL] " + " | ".join(ev["reasons"]))
                frames = portal_frames(cp)
                if len(frames) > 1:
                    say("   Frames del portal vistos: " + "; ".join(f"{w}/{f} menú={n} visibles={v}" for w, f, _hp, n, v in frames) + " · use «V <ventana> <frame>» si el menú está en otro.")
                say("   Corrija el menú (expándalo hasta ver la hoja) y escriba P otra vez; solo se repite este checkpoint.")
            continue
        if cmd == "L":
            pre = store.pre_for(selected)
            if pre is None:
                say("   Falta el PRE de este destino: expanda el menú y escriba P primero."); continue
            cp = await capture(context, registry, watcher, destination=selected, kind="ARRIVAL", store=store, attempt=1 + sum(1 for c in store.checkpoints if c["destination"] == selected and c["kind"] == "ARRIVAL"), linked_pre=pre["checkpoint_id"])
            pending_arrival = cp
            ev = core.evaluate_arrival(spec, pre, cp)
            if any("candidatas" in r for r in ev["reasons"]):
                cands = [(w["window_id"], f["frame_id"], f["url"].get("host"), f["url"].get("path")) for w, f in core.iter_frames(cp) if not core._is_noise(f) and core.host_is_portal(f["url"].get("host"))]
                say("   Hay varias candidatas. Elija con V <ventana> <frame>, por ejemplo «V W2 F5»:")
                for w, f, h, p in cands:
                    say(f"     {w}/{f}  {h}{p}")
                continue
            say(await _finish_arrival(store, pending_arrival, spec, pre, read_line, say)); pending_arrival = None
            continue
        say("   Comando no válido. Use un número, P, L, V, K, R, M, Q o X.")


def matrix_row(store, prior, key):
    return core.matrix(store, prior)["destinations"][key]


async def _finish_arrival(store: core.Store, cp: dict, spec: core.DestSpec, pre: dict, read_line, say) -> str:
    cp["same_journey_confirmed"] = None
    ev = core.evaluate_arrival(spec, pre, cp)
    say(f"   Llegada detectada: {(ev['arrival'] or {}).get('kind', '—')} {((ev['arrival'] or {}).get('host') or '')}{((ev['arrival'] or {}).get('path_pattern') or '')}")
    say("   ¿Esta llegada corresponde al clic que usted acaba de hacer desde ese menú (mismo recorrido)? S/N")
    answer = ((await read_line()) or "N").strip().upper()
    cp["same_journey_confirmed"] = answer == "S"
    if spec.effects_review:
        say("   ¿Confirma que abrir esta opción solo MUESTRA la pantalla inicial (no cargó, validó, generó ni envió nada)? S/N")
        cp["owner_attests_view_only"] = ((await read_line()) or "N").strip().upper() == "S"
    store.add(cp)
    ev = core.evaluate_arrival(spec, pre, cp)
    if ev["status"] == "COMPLETE":
        return f"   [LLEGADA {cp['checkpoint_id']} registrada y vinculada a {pre['checkpoint_id']}] riesgo de efectos: {ev['effects']['side_effect_risk']}"
    return f"   [LLEGADA {cp['checkpoint_id']} registrada como PARCIAL] " + " | ".join(ev["reasons"]) + "\n   Repita con L cuando la pantalla esté cargada (el PRE se conserva)."


def _stdin_reader(loop, queue) -> None:
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:  # noqa: BLE001
            line = ""
        line = line.lstrip(chr(0xFEFF))  # PowerShell 5.1 antepone un BOM al canalizar comandos por tuberia
        loop.call_soon_threadsafe(queue.put_nowait, line if line else "Q")
        if not line:
            return


def _cdp_alive(port: int) -> bool:
    return chromium.cdp_http_alive(port)


async def launch_or_attach(pw, out_dir: Path, attach_port: int | None, entry_url: str | None, say=print, headless: bool = False,
                           deadline_s: float = chromium.STARTUP_DEADLINE_S, _hooks: dict | None = None):
    """Chromium con depurador SOLO en 127.0.0.1 como proceso independiente (sobrevive a la herramienta): con Q el navegador autenticado se CONSERVA y
    `-Attach`/`-Resume` lo recupera sin volver a iniciar sesion. Perfil EFIMERO propio. Cada fallo se informa con su causa concreta (ver
    `m3_chromium_launch`). Devuelve (browser, context, launched|None, port)."""
    hooks = _hooks or {}
    if chromium.is_elevated():
        say("   AVISO: PowerShell está ejecutándose como ADMINISTRADOR. Esta herramienta no necesita permisos elevados y Chromium puede fallar con perfiles de otra "
            "cuenta; si falla, abra una PowerShell normal.")
    if attach_port is not None:
        port = chromium.load_attach_port(out_dir, attach_port)
        browser = await chromium.connect_cdp(pw, port, say=say)
        return browser, browser.contexts[0], None, port
    t0 = time.monotonic()
    launched = chromium.start_chromium(pw.chromium.executable_path, headless=headless, requested_port=hooks.get("requested_port"),
                                       argv_override=hooks.get("argv_override"), post_start=hooks.get("post_start"), hooks_deny_acl=hooks.get("deny_acl", False))
    try:
        port = await chromium.wait_until_ready(launched, deadline_s=deadline_s, say=say, progress_every=hooks.get("progress_every", 10.0))
        browser = await chromium.connect_cdp(pw, port, say=say, timeout_s=hooks.get("connect_timeout_s", chromium.CONNECT_TIMEOUT_S))
    except chromium.LaunchError:
        chromium.terminate_own(launched)  # SOLO el proceso que lanzo esta herramienta
        chromium.cleanup_profile(launched)
        raise
    launched.timings["connected_s"] = round(time.monotonic() - t0, 1)
    chromium.save_state(out_dir, launched)
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else await context.new_page()
    if entry_url:
        try:  # unica navegacion automatica: la pagina PUBLICA de entrada (nunca login ni menus)
            await page.goto(entry_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:  # noqa: BLE001
            say("Aviso: la página pública no cargó a tiempo; puede abrirla usted en la ventana.")
    say(f"   Chromium listo en 127.0.0.1:{port} (CDP en {launched.timings['cdp_up_s']} s; conexión completa en {launched.timings['connected_s']} s).")
    return browser, context, launched, port


async def finish_browser(browser, launched, port: int, mode: str, say=print, out_dir: Path | None = None) -> None:
    if mode == "KEEP":
        say(f"Navegador CONSERVADO (sesión SUNAT intacta). Para continuar sin autenticarse otra vez: .\\scripts\\m3_recon.ps1 -Resume   (o -Attach {port})")
        return  # solo se desconecta la herramienta; el navegador sigue vivo
    try:  # cierre ordenado por CDP (funciona tambien al recuperar una sesion, sin necesitar el PID)
        session = await browser.new_browser_cdp_session()
        await session.send("Browser.close")
    except Exception:  # noqa: BLE001
        try:
            await browser.close()
        except Exception:  # noqa: BLE001
            pass
    if launched is not None:
        for _ in range(20):
            if launched.proc.poll() is not None:
                break
            await asyncio.sleep(0.25)
        chromium.terminate_own(launched)
        chromium.cleanup_profile(launched)
    elif out_dir is not None:  # sesion recuperada: esperar el cierre por CDP y borrar el perfil propio registrado
        for _ in range(40):
            if not chromium.cdp_http_alive(port):
                break
            await asyncio.sleep(0.25)
        try:
            name = json.loads((out_dir / chromium.STATE_FILE).read_text(encoding="ascii")).get("profile", "")
        except (OSError, ValueError):
            name = ""
        if not chromium.cdp_http_alive(port):
            await asyncio.sleep(1.0)  # Chromium libera el perfil unos instantes despues de cerrar
            chromium.cleanup_profile_by_name(name)


async def diagnose(out_dir: Path) -> int:
    """Ensayo de arranque SIN SUNAT: ejecutable, carpeta temporal, lanzamiento real (sin ventana), CDP, conexion, pagina local y cierre. Informa tiempos y la
    causa concreta si algo falla. No toca checkpoints ni abre ninguna pagina de internet."""
    import platform
    import tempfile

    from playwright.async_api import async_playwright

    ok = True
    print(f"Windows/Python: {platform.platform()} · Python {platform.python_version()}")
    print(f"PowerShell elevada (Administrador): {'SÍ — no recomendado' if chromium.is_elevated() else 'no'}")
    print(f"Carpeta de perfiles efímeros: {chromium.profile_base()}")
    stale = [d for d in Path(tempfile.gettempdir()).glob(chromium.PROFILE_PREFIX + "*") if d.is_dir()]
    if stale:
        print(f"Restos de intentos anteriores en la carpeta temporal: {len(stale)} (inofensivos; creados por la versión anterior con una ACL que Chromium no podía abrir)")
    async with async_playwright() as pw:
        exe = Path(pw.chromium.executable_path)
        print(f"Ejecutable: {exe.name} · existe={exe.is_file()} · tamaño={exe.stat().st_size if exe.is_file() else 'n/a'}")
        try:
            t = Path(tempfile.mkdtemp(prefix="m3-recon-diag-"))
            (t / "x").write_text("ok")
            import shutil

            shutil.rmtree(t, ignore_errors=True)
            print("Carpeta temporal escribible: sí")
        except OSError as exc:
            print(f"Carpeta temporal escribible: NO ({type(exc).__name__})")
            ok = False
        state = out_dir / "cdp_port.txt"
        print("Sesión previa recuperable: " + ("sí (puerto %s)" % state.read_text().strip() if state.exists() and chromium.cdp_http_alive(int(state.read_text().strip() or 0)) else "no"))
        scratch = Path(tempfile.mkdtemp(prefix="m3-recon-diag-out-"))
        try:
            browser, context, launched, port = await launch_or_attach(pw, scratch, None, None, headless=True)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("about:blank")
            value = await page.evaluate("1 + 1")
            print(f"Arranque OK · puerto real {port} · CDP {launched.timings['cdp_up_s']} s · conexión {launched.timings['connected_s']} s · página local evalúa {value}")
            await finish_browser(browser, launched, port, "CLOSE", say=lambda *_a: None)
            print("Cierre OK (sin navegadores huérfanos de esta herramienta)")
        except chromium.LaunchError as exc:
            ok = False
            print("FALLO DE ARRANQUE: " + exc.render())
        finally:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)
    print("DIAGNÓSTICO: " + ("CORRECTO" if ok else "CON FALLOS"))
    return RC_OK if ok else RC_BAD_ARGS


async def amain(out_dir: Path, prior_dirs: list[Path], attach_port: int | None = None, entry_url: str = ENTRY_URL, public_only: bool = False) -> int:
    import json

    repo = Path(__file__).resolve().parent.parent
    out_dir = out_dir.resolve()
    if repo in out_dir.parents or out_dir == repo:
        print("La carpeta de salida no puede estar dentro del repositorio.", file=sys.stderr)
        return RC_BAD_ARGS
    out_dir.mkdir(parents=True, exist_ok=True)
    store = core.Store(out_dir / "m3_recon_v2.json")
    prior: dict = {}
    for d in prior_dirs:
        f = Path(d) / "m2_sol_recon.json"
        if f.exists():
            prior.update(core.prior_arrivals_from_v1(json.loads(f.read_text(encoding="utf-8"))))
    browsers = repo / ".pw-browsers-win-e2"
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    from playwright.async_api import async_playwright

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_stdin_reader, args=(loop, queue), daemon=True).start()
    async with async_playwright() as pw:
        try:
            browser, context, proc, port = await launch_or_attach(pw, out_dir, attach_port, entry_url)
        except chromium.LaunchError as exc:
            print("\nNO SE PUDO INICIAR/RECUPERAR CHROMIUM (antes de cualquier autenticación SUNAT):\n" + exc.render(), file=sys.stderr)
            print("Sus checkpoints anteriores NO se tocaron. Diagnóstico completo: .\\scripts\\m3_recon.ps1 -Diagnose", file=sys.stderr)
            return RC_BAD_ARGS
        if attach_port is not None:
            print(f"Sesión recuperada: ventanas abiertas = {len(v1.live_pages(context))}. No hace falta volver a autenticarse.")
        mode = await run_guided(context, v1.ReconConfig(), store, prior, queue.get, public_only=public_only)
        mx = core.matrix(store, prior)
        (out_dir / "m3_recon_matrix.json").write_text(json.dumps({"matrix": mx, "candidates": core.candidate_contracts(mx)}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(render_matrix(mx))
        if mode.startswith("RC:"):
            await finish_browser(browser, proc, port, "KEEP")  # ante cualquier error el navegador autenticado se conserva
            return int(mode[3:])
        await finish_browser(browser, proc, port, mode, out_dir=out_dir)
    return RC_OK


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="carpeta de salida FUERA del repositorio")
    ap.add_argument("--prior", action="append", default=[], help="carpeta(s) de un reconocimiento v1 (m2_sol_recon.json) cuya llegada se reutiliza")
    ap.add_argument("--attach", type=int, default=None, help="puerto del depurador de un Chromium YA abierto por esta herramienta (0 = leer cdp_port.txt)")
    ap.add_argument("--entry-url", default=None, help="SOLO pruebas: pagina de entrada LOCAL (loopback/about); por defecto la pagina publica de SUNAT")
    ap.add_argument("--public-only", action="store_true", help="no espera el inicio de sesion: observa solo la pagina PUBLICA (destino 4)")
    ap.add_argument("--diagnose", action="store_true", help="comprueba el arranque de Chromium sin tocar SUNAT y sale")
    args = ap.parse_args()
    if args.diagnose:
        return asyncio.run(diagnose(Path(args.out)))
    entry = ENTRY_URL
    if args.entry_url:
        host = (urlsplit(args.entry_url).hostname or "") if "://" in args.entry_url else ""
        if args.entry_url != "about:blank" and host not in {"127.0.0.1", "localhost"}:
            print("--entry-url solo admite 127.0.0.1/localhost/about:blank (nunca otro sitio).", file=sys.stderr)
            return RC_BAD_ARGS
        entry = args.entry_url
    return asyncio.run(amain(Path(args.out), [Path(p) for p in args.prior], args.attach, entry, args.public_only))


if __name__ == "__main__":
    sys.exit(main())
