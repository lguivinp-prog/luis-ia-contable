"""M2 -- reconocimiento ESTRUCTURAL de SOL asistido por un humano. SOLO OBSERVA.

Uso (python del venv nativo ya existente; no se instala nada):

    .\\.venv-win-e2\\Scripts\\python.exe scripts\\m2_sol_recon.py --out "$env:USERPROFILE\\m2-recon-out"

Garantias (verificadas por pruebas y por una auditoria estatica del propio script):
* CERO clics, CERO teclado automatico, CERO relleno de campos, CERO captura de credenciales o portapapeles. El humano
  hace clic en "Ingresar", escribe sus credenciales SOLO en la pagina real de SUNAT, resuelve cualquier desafio y
  navega a cada destino.
* Contexto nuevo y efimero: sin perfil, sin storageState, sin cookies leidas, sin trazas, sin video, sin capturas,
  sin cuerpos de red.
* La salida es JSON UTF-8 ESTRUCTURAL: ningun texto libre, valor de campo, query, fragmento, cookie ni HTML. Solo
  etiquetas de navegacion de una lista blanca explicita (ver `m2_recon_sanitizer.NAV_LABELS`).
* Las etiquetas por consola son un ENUM fijo; no se pide ni se guarda texto arbitrario.
* La carpeta de salida debe estar FUERA del repositorio. No commitear el archivo generado.

Observacion dinamica (correccion del bloqueo): usa la API ASINCRONA de Playwright para que los eventos del navegador
(paginas nuevas, popups, navegaciones, cierres) se procesen SIEMPRE, incluso mientras espera al humano. Nunca asume que
la Page inicial sigue siendo la activa: en cada ciclo relee TODAS las Pages vivas del BrowserContext. Informa su estado
(ESPERANDO_LOGIN_MANUAL, SESION_AUTENTICADA_DETECTADA, ESPERANDO_ETIQUETA, TIMEOUT_SIN_SESION_AUTENTICADA,
NAVEGADOR_CERRADO) y tiene timeouts explicitos.

NO abra el Buzon ni ninguna accion que envie, presente, pague o modifique."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m2_recon_sanitizer as san  # noqa: E402

ENTRY_URL = "https://www.sunat.gob.pe/sol.html"

OWNER_LABELS = (
    "AUTHENTICATED_SHELL",
    "MENU_SOL",
    "FICHA_RUC",
    "SIRE",
    "DECLARACIONES_Y_PAGOS",
    "DECLARA_FACIL",
    "TRAMITES_Y_CONSULTAS",
)

# Estados informados por consola (nunca queda "congelado" sin decir que espera).
ST_WAIT_LOGIN = "ESPERANDO_LOGIN_MANUAL"
ST_AUTHENTICATED = "SESION_AUTENTICADA_DETECTADA"
ST_WAIT_LABEL = "ESPERANDO_ETIQUETA"
ST_TIMEOUT = "TIMEOUT_SIN_SESION_AUTENTICADA"
ST_BROWSER_CLOSED = "NAVEGADOR_CERRADO"

RC_OK, RC_TIMEOUT, RC_BAD_ARGS, RC_BROWSER_CLOSED = 0, 1, 2, 3


@dataclass(frozen=True)
class ReconConfig:
    entry_hosts: frozenset[str] = frozenset({"www.sunat.gob.pe", "sunat.gob.pe"})
    auth_hosts: frozenset[str] = frozenset({"api-seguridad.sunat.gob.pe"})
    login_timeout_s: float = 900.0
    poll_s: float = 0.5
    status_every_s: float = 20.0
    settle_s: float = 3.0
    snapshot_timeout_s: float = 10.0
    label_timeout_s: float = 1800.0


def _is_web(url: str) -> bool:
    return urlsplit(url).scheme in {"http", "https"}


def classify_pages(urls: list[str], cfg: ReconConfig) -> dict[str, int]:
    """Clasifica las Pages vivas SOLO por host (estructural). `candidates` = paginas web que no son ni la entrada
    publica ni el login: la sesion autenticada puede estar en cualquiera de ellas (nueva Page, popup, reemplazo)."""
    counts = {"pages": len(urls), "entry": 0, "auth": 0, "candidates": 0, "other_blank": 0}
    for url in urls:
        if not _is_web(url):
            counts["other_blank"] += 1
            continue
        host = urlsplit(url).hostname or ""
        if host in cfg.auth_hosts:
            counts["auth"] += 1
        elif host in cfg.entry_hosts:
            counts["entry"] += 1
        else:
            counts["candidates"] += 1
    return counts


# Extractor de SOLO LECTURA (constante; nunca se compone con entrada externa). El texto de la pagina NO sale del
# navegador: solo se devuelve `text_probe`/`aria_probe` cuando el texto normalizado es IGUAL a una forma de la lista
# blanca recibida como argumento; en cualquier otro caso es null. No lee valores de campos, cookies ni storage.
_EXTRACT_JS = r"""
(allow) => {
  const allowed = new Set(allow);
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const probe = (s) => { const n = norm(s); return allowed.has(n) ? n : null; };
  const vis = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const structural = new Set(['a','button','input','select','textarea','form','nav','ul','ol','li','table','iframe','frame','h1','h2','h3','h4','section','header','footer','main','aside']);
  const out = [];
  const all = document.querySelectorAll('*');
  let scanned = 0;
  for (const el of all) {
    if (++scanned > 6000 || out.length >= 800) break;
    const tag = el.tagName.toLowerCase();
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.nodeValue).join(' ');
    const textProbe = probe(own);
    const ariaProbe = probe(el.getAttribute('aria-label'));
    if (!(structural.has(tag) || el.id || el.getAttribute('role') || textProbe || ariaProbe)) continue;
    let depth = 0; for (let p = el; p.parentElement; p = p.parentElement) depth++;
    const par = el.parentElement;
    out.push({
      tag, id: el.id || null,
      classes: (typeof el.className === 'string') ? el.className.split(/\s+/).filter(Boolean) : [],
      role: el.getAttribute('role'),
      type: tag === 'input' ? (el.getAttribute('type') || 'text') : null,
      text_probe: textProbe, aria_probe: ariaProbe,
      href: (tag === 'a' && textProbe) ? el.getAttribute('href') : null,
      visible: vis(el), depth,
      sibling_index: par ? Array.prototype.indexOf.call(par.children, el) : 0,
      parent_tag: par ? par.tagName.toLowerCase() : null, parent_id: par ? (par.id || null) : null,
      has_onclick: el.hasAttribute('onclick'),
    });
  }
  return { title: document.title, nodes: out, truncated: scanned > 6000 || out.length >= 800 };
}
"""


def live_pages(context) -> list:
    return [p for p in context.pages if not p.is_closed()]


async def _frame_view(frame, cfg: ReconConfig) -> dict:
    if frame.is_detached():
        return {"error": True}
    try:
        raw = await asyncio.wait_for(frame.evaluate(_EXTRACT_JS, san.NAV_TEXT_FORMS), cfg.snapshot_timeout_s)
        return san.sanitize_frame_view(raw)
    except Exception:  # noqa: BLE001 -- frame en navegacion/inaccesible/timeout: no debe tumbar el reconocimiento
        return {"error": True}


async def snapshot(context, label: str, cfg: ReconConfig | None = None) -> dict:
    """Relee TODAS las Pages vivas y TODOS sus frames en este instante (nunca una Page cacheada)."""
    cfg = cfg or ReconConfig()
    pages = []
    for page in live_pages(context):
        main_frame = page.main_frame
        entry = {"url": san.safe_url(page.url), "frames": []}
        for frame in list(page.frames):
            entry["frames"].append(
                {"is_main": frame == main_frame, "url": san.safe_url(frame.url), "view": await _frame_view(frame, cfg)}
            )
        pages.append(entry)
    return {"label": label, "date": date.today().isoformat(), "page_count": len(pages), "pages": pages}


def _write(out_file: Path, snapshots: list[dict]) -> None:
    out_file.write_text(san.dumps(snapshots), encoding="utf-8")


def _page_events(context, say) -> None:
    def watch(page) -> None:
        page.on("close", lambda _p: say("EVENTO: PAGE_CERRADA"))

    for page in context.pages:
        watch(page)

    def on_page(page) -> None:
        say("EVENTO: PAGE_NUEVA (nueva pestaña o popup)")
        watch(page)

    context.on("page", on_page)


async def run_recon(context, cfg: ReconConfig, out_file: Path, read_label, say=print) -> int:
    """`read_label()` es una corrutina que devuelve la siguiente linea del humano (inyectable en pruebas)."""
    snapshots: list[dict] = [{"tool": "m2_sol_recon", "sanitizer_version": san.SANITIZER_VERSION, "automated_clicks": 0}]
    _page_events(context, say)
    loop = asyncio.get_running_loop()

    if live_pages(context):
        snapshots.append(await snapshot(context, "PUBLIC_ENTRY", cfg))

    say(f"{ST_WAIT_LOGIN}: en Chromium haga clic en 'Ingresar' e inicie sesión usted mismo (no escriba credenciales aquí).")
    login_snapped = False
    ever_had_pages = bool(live_pages(context))
    started = loop.time()
    last_status = started
    authenticated = False
    while loop.time() - started < cfg.login_timeout_s:
        pages = live_pages(context)
        ever_had_pages = ever_had_pages or bool(pages)
        if not pages and ever_had_pages:
            say(f"{ST_BROWSER_CLOSED}: se cerraron todas las ventanas.")
            _write(out_file, snapshots)
            return RC_BROWSER_CLOSED
        counts = classify_pages([p.url for p in pages], cfg)
        if counts["auth"] and not login_snapped:
            await asyncio.sleep(cfg.settle_s)
            snapshots.append(await snapshot(context, "LOGIN_PAGE", cfg))
            login_snapped = True
            say("   [LOGIN_PAGE registrado]")
        if counts["candidates"]:
            authenticated = True
            break
        if loop.time() - last_status >= cfg.status_every_s:
            say(f"{ST_WAIT_LOGIN} (ventanas abiertas: {counts['pages']}; en login: {counts['auth']}; públicas: {counts['entry']})")
            last_status = loop.time()
        await asyncio.sleep(cfg.poll_s)

    if not authenticated:
        say(f"{ST_TIMEOUT}: no se detectó una sesión autenticada en {int(cfg.login_timeout_s)} s.")
        _write(out_file, snapshots)
        return RC_TIMEOUT

    say(f"{ST_AUTHENTICATED}: se detectó una página fuera de la entrada pública y del login.")
    await asyncio.sleep(cfg.settle_s)  # deja terminar la carga
    snapshots.append(await snapshot(context, "FIRST_AUTHENTICATED_PAGE", cfg))
    _write(out_file, snapshots)

    options = " | ".join(OWNER_LABELS)
    say(f"{ST_WAIT_LABEL}: navegue usted a mano a cada destino. Estando en él, escriba UNA etiqueta: {options}")
    say("   Escriba Q para terminar. NO abra el Buzón ni ninguna acción que envíe/presente/pague/modifique.")
    step = min(cfg.status_every_s, 5.0)
    while True:
        task = asyncio.ensure_future(read_label())
        waited = 0.0
        next_status = cfg.status_every_s
        while not task.done():
            await asyncio.wait({task}, timeout=step)
            waited += step
            if not live_pages(context):
                task.cancel()
                say(f"{ST_BROWSER_CLOSED}: se cerraron todas las ventanas.")
                _write(out_file, snapshots)
                return RC_BROWSER_CLOSED
            if not task.done() and waited >= cfg.label_timeout_s:
                task.cancel()
                say(f"{ST_TIMEOUT}: sin etiquetas durante {int(cfg.label_timeout_s)} s; se guarda lo registrado.")
                _write(out_file, snapshots)
                return RC_TIMEOUT
            if not task.done() and waited >= next_status:
                say(f"{ST_WAIT_LABEL} (ventanas abiertas: {len(live_pages(context))})")
                next_status += cfg.status_every_s
        try:
            label = (task.result() or "").strip().upper()
        except Exception:  # noqa: BLE001
            label = "Q"
        if label == "Q":
            _write(out_file, snapshots)
            say("Listo. Archivo estructural sanitizado generado en la carpeta indicada.")
            return RC_OK
        if label not in OWNER_LABELS:
            say("   Etiqueta no válida; use una de la lista o Q.")
            continue
        snapshots.append(await snapshot(context, label, cfg))
        _write(out_file, snapshots)
        say(f"   [{label} registrado]")
        say(f"{ST_WAIT_LABEL}: otra etiqueta o Q.")


def _stdin_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
    # Hilo DAEMON: un readline bloqueado nunca impide cerrar el proceso ni detiene los eventos de Playwright.
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:  # noqa: BLE001
            line = ""
        loop.call_soon_threadsafe(queue.put_nowait, line if line else "Q")
        if not line:
            return


async def amain(out_dir: Path) -> int:
    repo = Path(__file__).resolve().parent.parent
    out_dir = out_dir.resolve()
    if repo in out_dir.parents or out_dir == repo:
        print("La carpeta de salida no puede estar dentro del repositorio.", file=sys.stderr)
        return RC_BAD_ARGS
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "m2_sol_recon.json"

    # Reutiliza el Chromium de Playwright YA instalado del repo (no instala nada ni modifica PATH).
    browsers = repo / ".pw-browsers-win-e2"
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    from playwright.async_api import async_playwright

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_stdin_reader, args=(loop, queue), daemon=True).start()

    async def read_label() -> str:
        return await queue.get()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()  # efimero: sin perfil, sin storageState
        page = await context.new_page()
        try:
            await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception:  # noqa: BLE001
            print("Aviso: la página pública no cargó a tiempo; puede abrirla usted en la ventana.")
        rc = await run_recon(context, ReconConfig(), out_file, read_label)
        for closer in (context.close, browser.close):
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="carpeta de salida FUERA del repositorio")
    args = ap.parse_args()
    return asyncio.run(amain(Path(args.out)))


if __name__ == "__main__":
    sys.exit(main())
