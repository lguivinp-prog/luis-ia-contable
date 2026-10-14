"""M2 -- navegacion SUPERVISADA a un destino validado de SOL + traspaso al humano (modulo E3: puede referirse a
Playwright/SUNAT; ver `destination_contract` para la evidencia LIVE).

Orden (seccion 12 del brief): autenticar (flujo existente, UNA sola entrega de credencial) -> marcador POSITIVO del
shell (DOM; sin el, NINGUN clic) -> destino aprobado por el servidor (enum cerrado, sin URL/selector del frontend) ->
navegar con presupuesto de clics del contrato -> validar el destino estructuralmente -> (Ficha) extraer solo campos del
parser -> CONGELAR la automatizacion (presupuesto 0) -> control humano hasta que el humano cierre el navegador, se
cancele o venza -> limpieza. Tras congelar, `post_landing_clicks` debe ser 0 siempre: la interaccion manual del humano
nunca reactiva la automatizacion. Estado desconocido => fail closed (`PORTAL_CHANGED`), sin selectores alternativos."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from logging import getLogger

from windows_agent import destination_contract as dc
from windows_agent import diagnostics, interstitial
from windows_agent import ficha_caption_probe as probe
from windows_agent import ficha_parser
from windows_agent.broker_client import CredentialMaterial
from windows_agent.browser_executor import (
    SupervisedAuthExecutor,
    _authenticate,
    _AutomationStopped,
    _BrowserGone,
    _Cancelled,
    _NavigationBlocked,
    make_result,
)
from windows_agent.logging_setup import log_event
from windows_agent.portal_contract import AuthOutcome, AuthResult
from windows_agent.portal_identity import SurfaceKind
from windows_agent.portal_identity import _host as _host_of

_FICHA_FRAME_JS = r"""
(ids) => {
  const visible = (t) => { const r = t.getBoundingClientRect(); const s = getComputedStyle(t);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const counts = {};
  for (const id of ids) counts[id] = document.querySelectorAll('[id="' + id + '"]').length;
  const tables = Array.from(document.querySelectorAll('table.form-table')).filter(visible).length;
  return { counts, tables };
}
"""

# Vista de un frame de llegada de un contrato (M3): ids unicos + tablas visibles segun el selector del CONTRATO (constante cerrada, nunca del portal).
_ARRIVAL_FRAME_JS = r"""
([ids, tableSelector]) => {
  const visible = (t) => { const r = t.getBoundingClientRect(); const s = getComputedStyle(t);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const counts = {};
  for (const id of ids) counts[id] = document.querySelectorAll('[id="' + id + '"]').length;
  const tables = Array.from(document.querySelectorAll(tableSelector)).filter(visible).length;
  return { counts, tables };
}
"""

# Lee SOLO filas cuyo primer celda coincide EXACTAMENTE con un rotulo de la lista blanca (el texto de las demas
# celdas no sale del navegador). Los valores de las celdas siguientes son los datos oficiales autorizados.
# Ademas devuelve (en memoria del agente; jamas se registra) las celdas cuyo texto EMPIEZA por "FICHA RUC" + 11 digitos con su posicion
# (tabla, fila, celda): el RUC solo se acepta del encabezado oficial unico en su posicion observada (`ficha_parser.identify_ruc_from_header`).
# NO es una busqueda global de numeros de 11 digitos: exige el prefijo de rotulo del encabezado.
_FICHA_ROWS_JS = r"""
(args) => {
  const forms = args.forms; const sectionTitles = args.sectionTitles;
  const norm = (s) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/\s+/g, ' ').trim().toLowerCase().replace(/:$/, '').trim();
  // Texto de una celda: lo VISIBLE (`innerText`) y, solo si esta vacio por estar el bloque plegado (display:none), su `textContent` en el DOM.
  // Es lectura pura: no se despliega nada ni se pulsa nada. `hiddenWithText` se cuenta para diagnosticar secciones contraidas.
  const stats = { hiddenCellsWithText: 0 };
  const txt = (el) => { const v = el.innerText || ''; if (v.trim()) return v; const h = (el.textContent || ''); if (h.trim()) stats.hiddenCellsWithText++; return h; };
  const allowed = new Set(forms);
  const secondary = /^actividad economica secundaria( \d{1,2})?$/;
  const rows = [];
  const headers = [];
  const tables = Array.from(document.querySelectorAll('table'));
  for (const c of document.querySelectorAll('td,th')) {
    if (headers.length >= 8) break;
    const m = (c.innerText || '').replace(/\s+/g, ' ').trim().match(/^ficha ruc ?:? ?(\d{11})(?!\d)/i);
    if (!m) continue;
    const tr = c.parentElement; const table = c.closest('table');
    headers.push({ ruc: m[1], table: table ? tables.indexOf(table) : -1, row: tr ? Array.from(tr.parentElement.children).indexOf(tr) : -1,
                   cell: tr ? Array.from(tr.children).indexOf(c) : -1 });
  }
  for (const tr of document.querySelectorAll('tr')) {
    if (rows.length >= 300) break;
    const cells = Array.from(tr.children).filter(c => c.tagName === 'TD' || c.tagName === 'TH');
    if (cells.length < 2) continue;
    const caption = norm(txt(cells[0]));
    if (!allowed.has(caption) && !secondary.test(caption)) continue;
    const lines = [];
    for (const c of cells.slice(1)) for (const l of txt(c).split('\n')) { const t = l.trim(); if (t) lines.push(t.slice(0, 300)); }
    rows.push([caption, lines.slice(0, 40)]);
  }
  // Secciones TABULARES (tributos afectos, representantes legales, establecimientos anexos): tras cada TITULO conocido (en orden de
  // documento) se recogen las filas siguientes de cualquier ancho hasta el proximo titulo, con el indice de tabla. Los textos NO salen del
  // proceso del agente. `diag` son SOLO contadores para distinguir seccion ausente / plegada en el DOM / no renderizada.
  const grids = [];
  const titles = [];
  const known = new Set(sectionTitles);
  const diag = { tables: tables.length, hiddenTables: 0, hiddenTablesWithText: 0, sections: {} };
  tables.forEach((table) => {
    if (table.getClientRects().length === 0) { diag.hiddenTables++; if ((table.textContent || '').trim()) diag.hiddenTablesWithText++; }
  });
  let active = null;
  tables.forEach((table, tableIndex) => {
    for (const tr of table.querySelectorAll('tr')) {
      const cells = Array.from(tr.children).filter(c => c.tagName === 'TD' || c.tagName === 'TH');
      if (!cells.length) continue;
      if (cells.length === 1 || cells.length === 3) {
        const label = norm(txt(cells[0]));
        if (known.has(label)) {
          if (titles.length < 12) titles.push({ table: tableIndex, title: label });
          active = label;
          if (!diag.sections[label]) diag.sections[label] = { rows: 0, hiddenRows: 0, maxCells: 0 };
          continue;
        }
      }
      if (active && grids.length < 400) {
        const before = stats.hiddenCellsWithText;
        const texts = cells.slice(0, 14).map(c => txt(c).split('\n').map(l => l.trim()).filter(Boolean).slice(0, 4).join('\n').slice(0, 200));
        if (!texts.some(t => t)) continue;
        const entry = diag.sections[active];
        entry.rows++; entry.maxCells = Math.max(entry.maxCells, cells.length);
        if (stats.hiddenCellsWithText > before) entry.hiddenRows++;
        grids.push({ table: tableIndex, cells: texts });
      }
    }
  });
  return { rows, headers, grids, titles, diag };
}
"""

_NAV_LEVEL_CUERPO = "xpath=ancestor::li[contains(concat(' ', normalize-space(@class), ' '), ' liCuerpo ')]"


_SETTLE_SECONDS = 0.6  # margen de asentamiento tras validar una llegada en Page nueva (ventanas adicionales del mismo clic)


def _pump(executor: SupervisedAuthExecutor, seconds: float) -> None:
    """Espera ENTREGANDO los eventos de Playwright (paginas, frames, cierres). Con la API sincrona `time.sleep` NO los
    procesa: `page.frames`/`frame.url`/`is_closed()` quedarian obsoletos (un iframe aun sin URL, un navegador cerrado
    por el humano que nunca se detecta). `wait_for_timeout` si los bombea sin tocar la pagina."""
    try:
        pages = [p for p in executor._context.pages if not p.is_closed()] if executor._context is not None else []
    except Exception:  # noqa: BLE001
        raise _BrowserGone() from None
    if not pages:
        executor._sleep(seconds)
        return
    try:
        pages[0].wait_for_timeout(int(seconds * 1000))
    except Exception:  # noqa: BLE001 -- la pagina/navegador desaparecio mientras esperabamos
        raise _BrowserGone() from None


def _fail(detail: str, submissions: int = 0) -> AuthResult:
    return make_result(AuthOutcome.PORTAL_CHANGED, detail=detail, submissions=submissions)


class DestinationNavigator:
    def __init__(
        self,
        executor: SupervisedAuthExecutor,
        *,
        wait_seconds: float = 20.0,
        ficha_frame_hosts: frozenset[str] = dc.FICHA_FRAME_HOSTS,
        ficha_frame_scheme: str = "https",
    ) -> None:
        self._ex = executor
        self._wait = wait_seconds
        self._ficha_hosts = ficha_frame_hosts
        self._ficha_scheme = ficha_frame_scheme

    # --- shell ---

    def _landing_page(self):
        surface, pages = self._ex._surface(strict=False)
        if surface.kind is not SurfaceKind.LANDING:
            return None
        return pages[surface.page_index]

    def await_shell(self) -> tuple[object | None, dc.ShellCheck]:
        """Marcador POSITIVO del shell (DOM). Espera acotada a que el DOM asiente; fuera de ese margen, fail closed."""
        ex = self._ex
        deadline = ex._clock() + self._wait
        last = dc.ShellCheck(False, "landing page not found")
        while True:
            ex._checkpoint()
            page = self._landing_page()
            if page is not None:
                last = dc.check_shell_marker(ex.id_snapshot(page, dc.SHELL_SNAPSHOT_IDS))
                if last.ok:
                    return page, last
            if ex._clock() >= deadline:
                return None, last
            _pump(ex, ex._poll)

    def _wait_check(self, page, check) -> dc.ShellCheck:
        ex = self._ex
        deadline = ex._clock() + self._wait
        while True:
            ex._checkpoint()
            result = check(ex.id_snapshot(page, dc.SHELL_SNAPSHOT_IDS))
            if result.ok or ex._clock() >= deadline:
                return result
            _pump(ex, ex._poll)

    # --- navegacion ---

    def navigate(self, destination, page) -> str | None:
        """None => destino alcanzado y validado. str => detalle del fallo (PORTAL_CHANGED). `destination`: contrato (o su id)."""
        contract = destination if isinstance(destination, dc.DestinationContract) else dc.get_contract(str(destination))
        if contract.alias_of is not None:
            contract = dc.get_contract(contract.alias_of)
        if contract.id == "MENU_SOL":
            result = self._wait_check(page, dc.check_menu_sol_destination)
            return None if result.ok else result.detail
        if contract.id == "FICHA_RUC":
            return self._navigate_ficha(page)
        return self._navigate_contract(contract, page)

    # --- recorrido de clics con espera humana ante avisos (compartido por la Ficha y los destinos de contrato) ---

    def _click_path(self, page, steps: list, *, missing_header: str) -> str | None:
        """`steps`: proveedores que devuelven el Locator a pulsar en ese paso (del nivel mas externo a la hoja). Antes de cada clic hay una
        comprobacion de accionabilidad SIN efectos; si un aviso/modal de SUNAT cubre el menu NUNCA se pulsa nada en el: se informa "esperando al
        usuario", el navegador sigue abierto y se reintenta el MISMO paso cuando el humano lo resuelva (presupuesto de clics intacto)."""
        ex = self._ex
        step = 0
        human_deadline = ex._clock() + ex.human_wait_seconds
        was_blocked = False
        last_category_at = -1e9
        while step < len(steps):
            if was_blocked:
                # Tras una intervencion humana: la sesion y el shell autenticado deben seguir siendo los validados antes de continuar.
                recheck = dc.check_shell_marker(ex.id_snapshot(page, dc.SHELL_SNAPSHOT_IDS))
                if not recheck.ok:
                    return recheck.detail or "shell marker missing or ambiguous"
            try:
                locator = steps[step]()
                if locator is None:
                    return missing_header
                ex.guarded_click(locator)
                diagnostics.note_action(ex, f"MENU_CLICK_{step + 1}")
                step += 1
            except _AutomationStopped:
                return "navigation click budget exhausted"
            except _NavigationBlocked as blocked:
                was_blocked = True
                if ex._clock() - last_category_at >= 5.0:  # lectura de solo lectura, acotada: no en cada sondeo
                    last_category_at = ex._clock()
                    category = ex.classify_blocker(page)
                    log_event(getLogger("windows_agent"), "destination_navigation_blocked", step=step, blocker=blocked.description, category=category)
                    ex.announce_waiting_for_user(category if category in interstitial.WAITING_REASONS else interstitial.HUMAN_ACTION_REQUIRED)
                if ex._clock() >= human_deadline:
                    return "navigation blocked by an intermediate screen"
                ex._checkpoint()
                _pump(ex, max(ex._poll, 0.5))
        return None

    def _navigate_ficha(self, page) -> str | None:
        target = page.locator("li.nivel4 > span.spanNivelDescripcion").filter(
            has_text=re.compile(r"^\s*Ficha RUC\s*$", re.IGNORECASE)
        )
        if target.count() != 1:
            return "ficha menu entry not found or ambiguous"
        cuerpos = target.locator("xpath=..").locator(_NAV_LEVEL_CUERPO)
        levels = cuerpos.count()
        if levels < 1 or levels > dc.FICHA_MAX_NAV_CLICKS - 1:
            return "ficha menu depth does not match the contract"

        def header_step(index: int):
            def provider():
                header = cuerpos.nth(index).locator("xpath=preceding-sibling::li[1]")  # del nivel mas externo al mas interno
                if header.count() != 1:
                    return None
                caption = header.locator("span.spanNivelDescripcion")
                return caption.first if caption.count() > 0 else header

            return provider

        failure = self._click_path(page, [header_step(i) for i in range(levels)] + [lambda: target], missing_header="ficha menu header not found")
        if failure is not None:
            return failure
        top = self._wait_check(page, dc.check_ficha_ruc_top)
        if not top.ok:
            return top.detail
        frame_check = self._wait_ficha_frame(page)
        return None if frame_check.ok else frame_check.detail

    # --- destinos de CONTRATO (M3): secuencia validada + identidad positiva de llegada ---

    def _navigate_contract(self, contract: dc.DestinationContract, page) -> str | None:
        """Solo para contratos `LIVE_VALIDATED` con `entry` y `arrival` declarados. Cualquier otra cosa FALLA CERRADO."""
        entry, arrival = contract.entry, contract.arrival
        if not contract.enabled or entry is None or arrival is None:
            return "destination not validated"
        if entry.kind == "PUBLIC_CARD":
            return self._validate_public_landing(page, arrival)  # el clic de la tarjeta ya ocurrio ANTES del login; aqui solo se valida la plataforma
        if entry.kind not in {"MENU_LEAF", "HOME_LINK"}:
            return "destination entry kind not supported"  # fail-closed ante cualquier tipo de entrada desconocido
        exact = re.compile(rf"^\s*{re.escape(entry.label)}\s*$", re.IGNORECASE)
        pages_before = {id(p) for p in self._ex._pages()}
        if entry.kind == "MENU_LEAF":
            target = page.locator(f"li.nivel{entry.leaf_level} > span.spanNivelDescripcion").filter(has_text=exact)
            found = target.count()
            if found == 0:
                return "destination entry not found"  # ausente del menu de ESTA cuenta (sin permiso o el portal cambio): no se distingue sin evidencia
            if found != 1:
                return "destination entry ambiguous"
            cuerpos = target.locator("xpath=..").locator(_NAV_LEVEL_CUERPO)
            levels = cuerpos.count()
            if levels < entry.min_levels or levels > entry.max_levels:
                return "destination menu depth does not match the contract"

            def header_step(index: int):
                def provider():
                    header = cuerpos.nth(index).locator("xpath=preceding-sibling::li[1]")
                    if header.count() != 1:
                        return None
                    caption = header.locator("span.spanNivelDescripcion")
                    return caption.first if caption.count() > 0 else header

                return provider

            steps = [header_step(i) for i in range(levels)] + [lambda: target]
        elif entry.kind == "HOME_LINK":
            link = page.locator("div#divTodasOpciones a").filter(has_text=exact)
            found = link.count()
            if found == 0:
                return "destination entry not found"
            if found != 1:
                return "destination entry ambiguous"
            steps = [lambda: link]
        else:
            return "destination not validated"
        failure = self._click_path(page, steps, missing_header="destination menu header not found")
        if failure is not None:
            return failure
        if arrival.kind == "FRAME":
            top = self._wait_check_ids(page, lambda snap: dc.check_application_top(snap, arrival), arrival)
            if not top.ok:
                return top.detail
            return self._wait_arrival_frame(page, arrival)
        return self._wait_arrival_page(pages_before, arrival)

    def _validate_public_landing(self, page, arrival: dc.ArrivalSpec) -> str | None:
        """Entrada publica: la PLATAFORMA es el landing autenticado. Identidad POSITIVA = esquema + host + ruta + ids unicos (los marcadores del shell ya se
        validaron en `await_shell`). Ninguna otra Page del portal puede existir salvo la pagina publica de entrada. Cero clics: la automatizacion TERMINA."""
        ex = self._ex
        deadline = ex._clock() + self._wait
        detail = "destination page not ready"
        while True:
            ex._checkpoint()
            strangers = [p for p in ex._pages() if p is not page and _host_of(p.url)[1] not in ex._contract.entry_hosts]
            if strangers:
                return "unexpected page after navigation"
            try:
                counts = {i: len(ex.id_snapshot(page, tuple(arrival.required_once_ids)).elements.get(i, ())) for i in arrival.required_once_ids}
                check = dc.check_arrival_page(page.url, counts, arrival)
            except Exception:  # noqa: BLE001 -- la Page aun navega
                check = dc.ShellCheck(False, "destination page not ready")
            if check.ok:
                _pump(ex, _SETTLE_SECONDS)  # asentamiento: ninguna ventana ajena puede aparecer justo despues de validar la plataforma
                if [p for p in ex._pages() if p is not page and _host_of(p.url)[1] not in ex._contract.entry_hosts]:
                    return "unexpected page after navigation"
                return None
            detail = check.detail or detail
            if ex._clock() >= deadline:
                return detail
            _pump(ex, ex._poll)

    def _wait_check_ids(self, page, check, arrival: dc.ArrivalSpec) -> dc.ShellCheck:
        ex = self._ex
        ids = tuple(dict.fromkeys(dc.SHELL_SNAPSHOT_IDS + (arrival.breadcrumb_id,)))
        deadline = ex._clock() + self._wait
        while True:
            ex._checkpoint()
            result = check(ex.id_snapshot(page, ids))
            if result.ok or ex._clock() >= deadline:
                return result
            _pump(ex, ex._poll)

    def _arrival_frame_view(self, frame, arrival: dc.ArrivalSpec) -> dc.FichaFrameView:
        raw = frame.evaluate(_ARRIVAL_FRAME_JS, [list(arrival.required_once_ids), arrival.table_selector])
        return dc.FichaFrameView(url=frame.url, once_id_counts=dict(raw["counts"]), visible_form_tables=int(raw["tables"]))

    def _wait_arrival_frame(self, page, arrival: dc.ArrivalSpec) -> str | None:
        ex = self._ex
        deadline = ex._clock() + self._wait
        while True:
            ex._checkpoint()
            matches = []
            for frame in page.frames:
                if frame == page.main_frame or not frame.url.startswith(arrival.scheme + "://"):
                    continue
                try:
                    if dc.check_arrival_frame(self._arrival_frame_view(frame, arrival), arrival).ok:
                        matches.append(frame)
                except Exception:  # noqa: BLE001 -- frame en navegacion/inaccesible: no coincide
                    continue
            if len(matches) == 1:
                return None
            if ex._clock() >= deadline:
                return "destination frame identity not validated"
            _pump(ex, ex._poll)

    def _wait_arrival_page(self, pages_before: set[int], arrival: dc.ArrivalSpec) -> str | None:
        """Llegada en una NUEVA Page: exactamente una Page nueva, con el origen/ruta/ids del contrato. Una segunda Page inesperada falla cerrado."""
        ex = self._ex
        deadline = ex._clock() + self._wait
        while True:
            ex._checkpoint()
            new_pages = [p for p in ex._pages() if id(p) not in pages_before]
            if len(new_pages) > 1:
                return "unexpected page after navigation"
            if len(new_pages) == 1:
                new = new_pages[0]
                try:
                    counts = {i: len(ex.id_snapshot(new, tuple(arrival.required_once_ids)).elements.get(i, ())) for i in arrival.required_once_ids}
                    check = dc.check_arrival_page(new.url, counts, arrival)
                except Exception:  # noqa: BLE001 -- Page aun navegando
                    check = dc.ShellCheck(False, "destination page not ready")
                if check.ok:
                    # asentamiento: una segunda ventana abierta por el mismo clic puede llegar DESPUES de validar la primera; la ambiguedad falla cerrado
                    _pump(ex, _SETTLE_SECONDS)
                    if len([p for p in ex._pages() if id(p) not in pages_before]) > 1:
                        return "unexpected page after navigation"
                    return None
                if ex._clock() >= deadline:
                    return check.detail or "destination page not opened"
            elif ex._clock() >= deadline:
                return "destination page not opened"
            _pump(ex, ex._poll)

    def _ficha_frames(self, page) -> list:
        return [f for f in page.frames if f != page.main_frame and f.url.startswith(self._ficha_scheme + "://")]

    def _frame_view(self, frame) -> dc.FichaFrameView:
        raw = frame.evaluate(_FICHA_FRAME_JS, list(dc.FICHA_FRAME_ONCE_IDS))
        return dc.FichaFrameView(url=frame.url, once_id_counts=dict(raw["counts"]), visible_form_tables=int(raw["tables"]))

    def _matching_ficha_frame(self, page):
        """Exactamente UN frame debe cumplir el contrato (origen + ruta + marcadores + tablas)."""
        matches = []
        for frame in self._ficha_frames(page):
            try:
                if dc.check_ficha_ruc_frame(self._frame_view(frame), hosts=self._ficha_hosts, scheme=self._ficha_scheme).ok:
                    matches.append(frame)
            except Exception:  # noqa: BLE001 -- frame en navegacion/inaccesible: no coincide
                continue
        return matches[0] if len(matches) == 1 else None

    def _wait_ficha_frame(self, page) -> dc.ShellCheck:
        ex = self._ex
        deadline = ex._clock() + self._wait
        while True:
            ex._checkpoint()
            if self._matching_ficha_frame(page) is not None:
                return dc.ShellCheck(True)
            if ex._clock() >= deadline:
                return dc.ShellCheck(False, "ficha frame identity not validated")
            _pump(ex, ex._poll)

    def extract_ficha(self, page) -> tuple[ficha_parser.FichaExtraction | None, str | None]:
        """(extraccion, detalle de fallo). Solo se extrae cuando el contenido esta CARGADO: espera acotada y pasiva (sin clics) a que la
        Ficha pinte estado, tipo y denominacion. Un documento parcial, un menu o un contenedor vacio NO autorizan la extraccion."""
        ex = self._ex
        deadline = ex._clock() + self._wait
        last: ficha_parser.FichaExtraction | None = None
        while True:
            ex._checkpoint()
            frame = self._matching_ficha_frame(page)
            if frame is None:
                return None, "ficha frame identity not validated"
            raw = frame.evaluate(_FICHA_ROWS_JS, {"forms": ficha_parser.CAPTION_FORMS, "sectionTitles": sorted(ficha_parser.SECTION_TITLES)})
            rows = [(str(c), [str(x) for x in lines]) for c, lines in raw["rows"]]
            last = ficha_parser.parse_ficha(rows, list(raw["headers"]), list(raw.get("grids", [])), list(raw.get("titles", [])))
            if ficha_parser.content_ready(last):
                self._log_section_diagnostics(raw.get("diag"), last)
                return last, None
            if ex._clock() >= deadline:
                return last, "ficha content not ready"
            _pump(ex, ex._poll)

    def _log_section_diagnostics(self, diag, extraction) -> None:
        """Solo CONTADORES (nunca textos) para distinguir: seccion ausente (0 filas), plegada pero presente en el DOM (`hiddenRows`>0),
        presente y visible pero no interpretada (`rows`>0 sin campos) o interpretada. Una sola vez por lectura (el ultimo resultado)."""
        try:
            if not isinstance(diag, dict):
                return
            sections = {}
            for title, item in (diag.get("sections") or {}).items():
                key = ficha_parser.SECTION_TITLES.get(str(title))
                if key:
                    sections[key] = {"rows": int(item.get("rows", 0)), "hidden_rows": int(item.get("hiddenRows", 0)), "max_cells": int(item.get("maxCells", 0)),
                                     "parsed": len(extraction.fields.get(key) or [])}
            log_event(getLogger("windows_agent"), "ficha_section_diagnostics", tables=int(diag.get("tables", 0)), hidden_tables=int(diag.get("hiddenTables", 0)),
                      hidden_tables_with_text=int(diag.get("hiddenTablesWithText", 0)), sections=json.dumps(sections, sort_keys=True))
        except Exception:  # noqa: BLE001 -- diagnostico opcional
            return

    def probe_ficha_captions(self, page, extraction: ficha_parser.FichaExtraction | None = None) -> None:
        """Solo desarrollo (apagada salvo `SOL_AGENT_FICHA_PROBE_DIR`): escribe el informe SANITIZADO de rotulos y forma
        estructural (ver `ficha_caption_probe`). Lectura pura: ningun clic; nunca lanza ni altera la sesion."""
        directory = os.environ.get(probe.PROBE_ENV)
        if not directory:
            return
        try:
            frame = self._matching_ficha_frame(page)
            if frame is None:
                return
            summary = {}
            if extraction is not None:
                summary = {
                    "captions_found": list(extraction.captions_found),
                    "malformed": list(extraction.malformed),
                    "field_keys": sorted(extraction.fields),
                    "ruc_identified": "ruc" in extraction.fields,
                    "ruc_source": extraction.ruc_source,
                    "parser_captions_live_validated": ficha_parser.CAPTIONS_LIVE_VALIDATED,
                }
            probe.write_probe(directory, probe.build_probe(frame.evaluate(probe.CENSUS_JS), summary))
        except Exception:  # noqa: BLE001 -- diagnostico opcional: jamas afecta a la sesion
            return


def run_destination_session(
    executor: SupervisedAuthExecutor,
    credential_provider: Callable[[], AbstractContextManager[CredentialMaterial]],
    preauth_ready: Callable[[], bool] | None,
    *,
    destination: str,
    extract_ficha: bool,
    report_ficha: Callable[[dict], bool],
    report_human_control: Callable[[], bool],
    human_control_timeout_seconds: float = 30 * 60,
    acceptance: bool = False,
    navigator_wait_seconds: float = 20.0,
    ficha_frame_hosts: frozenset[str] = dc.FICHA_FRAME_HOSTS,
    ficha_frame_scheme: str = "https",
) -> AuthResult:
    """Frontera completa de una sesion con destino. Destino desconocido => fail closed ANTES de cualquier navegador."""
    logger = getLogger("windows_agent")
    try:
        dest = dc.get_contract(destination)
        if dest.alias_of is not None:  # defensa: el backend ya resuelve los alias; jamas se ejecuta un contrato propio para un alias
            dest = dc.get_contract(dest.alias_of)
    except ValueError:
        return make_result(AuthOutcome.PORTAL_CHANGED, detail="destination not allowed")
    if not dest.enabled and acceptance and dc.acceptance_eligible(dest):
        dest = dc.for_acceptance(dest)  # prueba real autorizada por el propietario en el backend; solo para esta sesion
    if not dest.enabled:
        # Conocido pero SIN evidencia estructural real: el agente se niega ANTES de abrir el navegador o pedir la credencial.
        return diagnostics.annotate(make_result(AuthOutcome.PORTAL_CHANGED, detail="destination not validated"), observation=None)
    if extract_ficha and not dest.extraction:
        return make_result(AuthOutcome.PORTAL_CHANGED, detail="extraction only allowed for FICHA_RUC")
    if dest.entry is not None and dest.entry.kind == "PUBLIC_CARD" and dest.arrival is not None:
        # Entrada publica: el contrato (no el frontend) fija la tarjeta y el landing esperados ANTES de abrir el navegador.
        executor.configure_public_entry(heading_text=dest.entry.label, landing_path_prefix=dc.public_landing_prefix(dest.arrival))

    state = {"reached": False, "ficha": False, "shell": False}
    try:
        result = _authenticate(executor, credential_provider, preauth_ready)
        if result.outcome is AuthOutcome.AUTH_SUCCEEDED:
            result = _reach_and_hold(
                executor, dest, extract_ficha, report_ficha, report_human_control, human_control_timeout_seconds,
                navigator_wait_seconds, state, ficha_frame_hosts, ficha_frame_scheme,
            )
        if result.outcome not in {AuthOutcome.AUTH_SUCCEEDED, AuthOutcome.CANCELLED}:
            # Diagnostico estructural saneado, capturado ANTES de cerrar el navegador (solo contadores/booleanos).
            result = diagnostics.annotate(
                result, observation=diagnostics.observation_of(executor), shell_reached=state["shell"], last_action=diagnostics.last_action_of(executor)
            )
        elif state["shell"]:
            result = replace(result, shell_reached=True)
    except BaseException as exc:
        observation = diagnostics.observation_of(executor) if isinstance(exc, Exception) else None
        executor.close()
        if isinstance(exc, Exception) and not isinstance(exc, (_Cancelled, _BrowserGone, _AutomationStopped)):
            # Cualquier error inesperado (p. ej. un timeout de Playwright) se convierte en un fallo CONTROLADO y reportable:
            # el navegador ya se cerro y el runtime informa FAILED al broker (la sesion nunca queda "en curso").
            log_event(logger, "destination_session_unexpected_error", error=type(exc).__name__)
            timed_out = type(exc).__name__ == "TimeoutError"
            failure = make_result(
                AuthOutcome.PORTAL_CHANGED if timed_out else AuthOutcome.BROWSER_FAILURE,
                detail="navigation timed out" if timed_out else "unexpected executor error",
                submissions=executor.credential_submissions,
            )
            return replace(
                diagnostics.annotate(
                    failure, observation=observation, shell_reached=state["shell"], last_action=diagnostics.last_action_of(executor)
                ),
                destination_clicks=executor.destination_clicks,
                destination_reached=state["reached"],
                ficha_reported=state["ficha"],
            )
        raise
    cleaned = executor.close()
    result = replace(
        result,
        credential_submissions=executor.credential_submissions,
        post_landing_clicks=executor.post_landing_clicks,
        destination_clicks=executor.destination_clicks,
        destination_reached=state["reached"],
        ficha_reported=state["ficha"],
    )
    log_event(logger, "destination_session_finished", destination=dest.id, outcome=result.outcome.value,
              destination_clicks=result.destination_clicks, post_destination_clicks=result.post_landing_clicks)
    if not cleaned and result.outcome is AuthOutcome.AUTH_SUCCEEDED:
        return replace(make_result(AuthOutcome.BROWSER_FAILURE, detail="CLEANUP_FAILED"),
                       credential_submissions=executor.credential_submissions)
    return result


def _reach_and_hold(
    executor, dest, extract_ficha, report_ficha, report_human_control, hc_timeout, wait_s, state, ficha_hosts, ficha_scheme
) -> AuthResult:
    submissions = executor.credential_submissions
    navigator = DestinationNavigator(
        executor, wait_seconds=wait_s, ficha_frame_hosts=ficha_hosts, ficha_frame_scheme=ficha_scheme
    )
    try:
        page, shell = navigator.await_shell()
        if page is None:
            return _fail(shell.detail or "authenticated shell marker not found", submissions)
        state["shell"] = True
        diagnostics.note_action(executor, "SHELL_VALIDATED")
        executor.arm_destination_navigation(dest.max_clicks)
        failure = navigator.navigate(dest, page)
        if failure is not None:
            executor.freeze_automation()
            return _fail(failure, submissions)
        state["reached"] = True
        diagnostics.note_action(executor, "DESTINATION_VALIDATED")
        if dest.id == "FICHA_RUC" and not extract_ficha:
            navigator.probe_ficha_captions(page)
        if extract_ficha:
            extraction, not_ready = navigator.extract_ficha(page)
            diagnostics.note_action(executor, "FICHA_EXTRACTED")
            navigator.probe_ficha_captions(page, extraction)
            executor.freeze_automation()
            if not_ready is not None:
                return _fail(not_ready, submissions)
            if extraction is None or "ruc" not in extraction.fields:
                return _fail("ficha extraction did not identify the taxpayer", submissions)
            payload = {"schema_version": extraction.schema_version, "fields": extraction.fields,
                       "malformed": extraction.malformed}
            state["ficha"] = bool(report_ficha(payload))
            if not state["ficha"]:
                return make_result(AuthOutcome.BROWSER_FAILURE, detail="ficha report rejected", submissions=submissions)
        executor.freeze_automation()  # el destino esta validado: la automatizacion TERMINA aqui
        if not report_human_control():
            return make_result(AuthOutcome.BROWSER_FAILURE, detail="human control report rejected", submissions=submissions)
        return _hold_for_human(executor, hc_timeout, submissions)
    except _Cancelled:
        executor.freeze_automation()
        return make_result(AuthOutcome.CANCELLED, submissions=submissions)
    except _BrowserGone:
        executor.freeze_automation()
        # El navegador desaparecio ANTES de entregar el control (no es el cierre humano normal).
        return make_result(AuthOutcome.BROWSER_FAILURE, submissions=submissions)


def _hold_for_human(executor, timeout_seconds: float, submissions: int) -> AuthResult:
    """Control humano: NO se hace ningun clic. Termina cuando el humano cierra el navegador (resultado normal), se
    cancela la sesion o vence el limite (el navegador se cierra)."""
    deadline = executor._clock() + timeout_seconds
    while True:
        try:
            executor._checkpoint()  # heartbeats (cancel_notice) + cancelacion
            if not executor._pages():
                break
        except _Cancelled:
            return make_result(AuthOutcome.CANCELLED, submissions=submissions)
        except _BrowserGone:
            break  # el humano cerro el navegador: fin NORMAL del control humano
        if executor._clock() >= deadline:
            break
        try:
            _pump(executor, executor._poll)
        except _BrowserGone:
            break  # el humano cerro el navegador mientras esperabamos
    return make_result(AuthOutcome.AUTH_SUCCEEDED, submissions=submissions)
