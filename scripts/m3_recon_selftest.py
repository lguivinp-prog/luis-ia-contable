"""M3 v2 -- AUTOPRUEBA del reconocedor de checkpoints contra un portal LOCAL SINTETICO (loopback, sin SUNAT, sin credenciales reales).

    .\\.venv-win-e2\\Scripts\\python.exe scripts\\m3_recon_selftest.py

Un "propietario" simulado (el propio script de prueba) hace los clics con Playwright; el reconocedor solo observa. Se comprueba: captura PRE con menu
colapsable expandido y hoja visible, hoja con enlace `javascript:`, nodo duplicado dentro de un `li`, llegada en frame / en nueva ventana (popup),
frames de ruido del shell, PRE oculto (parcial), repeticion de un solo checkpoint, reanudacion sin perder registros, red no-GET, SANITIZACION (RUC,
nombres, correos, cookies, tokens, valores de campos, HTML) y la ausencia de clics/teclado/relleno del propio reconocedor."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sys
import tempfile
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
browsers = HERE.parent / ".pw-browsers-win-e2"
if browsers.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))

import m2_recon_sanitizer as san  # noqa: E402
import m2_sol_recon as v1  # noqa: E402
import m3_chromium_launch as chromium  # noqa: E402
import m3_recon_core as core  # noqa: E402
import m3_sol_recon as rec  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

SECRETS = ["20123456789", "JUAN PEREZ GOMEZ", "juan.perez@example.com", "tok-ABCDEF123456", "sessioncookie", "Clave-Secreta-9", "AV. LOS OLIVOS 123"]


def menu_html(variant: str) -> str:
    # Menu estilo SOL: li.nivelN > span.spanNivelDescripcion, cuerpos li.nivelNCuerpo.liCuerpo ocultos hasta expandir.
    dup = '<span class="spanNivelDescripcion" style="display:none">Declaraciones y Pagos</span>' if variant != "nodup" else ""
    return f"""
<div id="divContainerMenu"><ul id="ulSubmenu">
 <li class="nivel1" id="nivel1_10"><span class="spanNivelDescripcion" onclick="tog('nivel1Cuerpo_10')">Empresas</span></li>
 <li class="nivel1Cuerpo liCuerpo" id="nivel1Cuerpo_10" style="display:none"><ul>
  <li class="nivel2" id="nivel2_10_1"><span class="spanNivelDescripcion" onclick="tog('nivel2Cuerpo_10_1')">Sistema Integrado de Registros Electrónicos</span></li>
  <li class="nivel2Cuerpo liCuerpo" id="nivel2Cuerpo_10_1" style="display:none"><ul>
   <li class="nivel3" id="nivel3_10_1_1"><span class="spanNivelDescripcion" onclick="tog('nivel3Cuerpo_10_1_1')">Registro de Ventas e Ingresos Electrónico</span></li>
   <li class="nivel3Cuerpo liCuerpo" id="nivel3Cuerpo_10_1_1" style="display:none"><ul>
     <li class="nivel4" id="nivel4_10_1_1_1"><a href="javascript:void(0)" onclick="openApp('/app/libroventas/gestionar-rvie/generacion-registro')"><span class="spanNivelDescripcion">Gestión de Ventas e Ingresos Electrónicos</span></a></li>
   </ul></li>
   <li class="nivel3" id="nivel3_10_1_2"><span class="spanNivelDescripcion" onclick="tog('nivel3Cuerpo_10_1_2')">Registro de Compras Electrónico</span></li>
   <li class="nivel3Cuerpo liCuerpo" id="nivel3Cuerpo_10_1_2" style="display:none"><ul>
     <li class="nivel4" id="nivel4_10_1_2_1"><a href="javascript:void(0)" onclick="openApp('/app/librocompras/gestionlibrocompras')"><span class="spanNivelDescripcion">Gestión de Compras</span></a></li>
   </ul></li>
   <li class="nivel3" id="nivel3_10_1_3"><a href="javascript:void(0)" onclick="openApp('/app/generacionlibro/inicio', {'true' if variant == 'genpost' else 'false'})"><span class="spanNivelDescripcion">Generación de Registros Electrónicos</span></a></li>
  </ul></li>
  <li class="nivel2" id="nivel2_10_2"><span class="spanNivelDescripcion" onclick="tog('nivel2Cuerpo_10_2')">Mi menu</span></li>
  <li class="nivel2Cuerpo liCuerpo" id="nivel2Cuerpo_10_2" style="display:none"><ul>
   <li class="nivel3" id="nivel3_86_2_1" onclick="window.open('/platform','_blank')"><span class="spanNivelDescripcion">Declaraciones y Pagos</span>{dup}</li>
  </ul></li>
 </ul></li>
</ul></div>
<div id="iDivApplication" style="display:none"><iframe id="iframeApplication" style="width:100%;height:200px"></iframe></div>
<iframe src="/noise/campanhaNotificacion" style="width:1px;height:1px"></iframe><iframe src="/noise/gettime.pl" style="width:1px;height:1px"></iframe>
<script>
function tog(id){{var e=document.getElementById(id);e.style.display=e.style.display==='none'?'block':'none';}}
function openApp(path,post){{ if(post){{ fetch('/api/accion',{{method:'POST',body:'x=1'}}); }}
  document.getElementById('divContainerMenu').style.display='none'; var d=document.getElementById('iDivApplication'); d.style.display='block'; document.getElementById('iframeApplication').src=path; }}
</script>"""



def public_html(variant: str = "ok") -> str:
    """Pagina PUBLICA de SOL: tarjetas con encabezado + control INGRESAR (span dentro de <a>), como la observada (5 tarjetas, una sin rotulo conocido)."""
    def card(title, hidden_btn=False, extra_h3=None, hidden_card=False):
        h3 = f"<h3>{extra_h3}</h3>" if extra_h3 else ""
        disp = ' style="display:none"' if hidden_btn else ""
        box = ' style="display:none"' if hidden_card else ""
        return f'<div class="tarjeta"{box}><h2 class="w-full uppercase">{title}</h2>{h3}<div><a class="text-xl text-bold"{disp}><span class="mr-4">INGRESAR</span></a></div></div>'
    cards = [card("Servicios generales"), card("Trámites y Consultas"), card("Otros", extra_h3="Empresas")]
    if variant == "ok":
        cards.insert(1, card("Mis Declaraciones y Pagos"))
    elif variant == "dup":
        cards += [card("Mis Declaraciones y Pagos"), card("Mis Declaraciones y Pagos")]
    elif variant == "hiddenbtn":
        cards.append(card("Mis Declaraciones y Pagos", hidden_btn=True))
    elif variant == "hiddencard":
        cards.append(card("Mis Declaraciones y Pagos", hidden_card=True))
    return "<section>" + "".join(cards) + "</section>"


def stale_app_shell(first: str, second: str) -> str:
    """Shell con UN iframe que primero carga una aplicacion SIRE (RVIE) y despues otra (RCE): ambas comparten los ids de plataforma."""
    return f"""<div id="divContainerMenu"></div><div id="iDivApplication"><iframe id="iframeApplication" src="{first}" style="width:100%;height:200px"></iframe></div>
<script>function swap(p){{document.getElementById('iframeApplication').src=p;}}</script>"""


def real_menu_html() -> str:
    """Estructura observada en el SOL REAL: li.nivelN > span.spanNivelDescripcion SIN <a> ni onclick (el clic se enlaza por script), cuerpos plegables
    `li.nivelNCuerpo.liCuerpo`, nivel2 sin rotulo conocido y una CABECERA y su HOJA con el mismo rotulo (Generacion de Registros)."""
    def leaf(i, text):
        return f'<li class="nivel4" id="{i}"><span class="spanNivelDescripcion">{text}</span></li>'
    return f"""
<div id="divContainerMenu"><ul id="ulSubmenu">
 <li class="nivel1" id="nivel1_60"><span class="spanNivelDescripcion">Sistema Integrado de Registros Electrónicos</span></li>
 <li class="nivel1Cuerpo liCuerpo" id="nivel1Cuerpo_60" style="display:none"><ul>
  <li class="nivel2" id="nivel2_60_2"><span class="spanNivelDescripcion">Registros</span></li>
  <li class="nivel2Cuerpo liCuerpo" id="nivel2Cuerpo_60_2" style="display:none"><ul>
   <li class="nivel3" id="nivel3_60_2_1"><span class="spanNivelDescripcion">Registro de Ventas e Ingresos Electrónico</span></li>
   <li class="nivel3Cuerpo liCuerpo" id="nivel3Cuerpo_60_2_1" style="display:none"><ul>{leaf("nivel4_60_2_1_1_1", "Gestión de Ventas e Ingresos Electrónicos")}</ul></li>
   <li class="nivel3" id="nivel3_60_2_2"><span class="spanNivelDescripcion">Registro de Compras Electrónico</span></li>
   <li class="nivel3Cuerpo liCuerpo" id="nivel3Cuerpo_60_2_2" style="display:none"><ul>{leaf("nivel4_60_2_2_1_1", "Gestión de Compras")}</ul></li>
   <li class="nivel3" id="nivel3_60_2_3"><span class="spanNivelDescripcion">Generación de Registros Electrónicos</span></li>
   <li class="nivel3Cuerpo liCuerpo" id="nivel3Cuerpo_60_2_3" style="display:none"><ul>{leaf("nivel4_60_2_3_1_1", "Generación de Registros Electrónicos")}</ul></li>
  </ul></li>
 </ul></li>
</ul></div>
<script>document.querySelectorAll('li.nivel4').forEach(function(li){{li.addEventListener('click',function(){{}});}});</script>"""


def page(body: str, title: str = "x") -> bytes:
    return f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>".encode("utf-8")


def app_page(kind: str) -> bytes:
    secret = f"<table class='data'><tr><td>RUC {SECRETS[0]}</td><td>{SECRETS[1]}</td></tr><tr><td>{SECRETS[2]}</td><td>{SECRETS[6]}</td></tr></table><input value='{SECRETS[5]}'>"
    if kind == "gen_file":
        secret += "<input type='file' id='archivo-carga'>"
    return page(f"<div id='app-root'></div><button id='btn-aceptar'>Aceptar</button><a id='nav-generacion-registro'>g</a><a id='nav-informacion-gestion'>i</a>{secret}")


class Handler(BaseHTTPRequestHandler):
    variant = "default"
    posts: list = []

    def log_message(self, *a):  # noqa: D401
        pass

    def do_POST(self):  # noqa: N802
        Handler.posts.append(self.path)
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = b"nf"
        if path == "/shell":
            body = page(menu_html(Handler.variant), "SUNAT - Menú SOL")
        elif path.startswith("/app/generacion/"):
            body = app_page("gen_file" if Handler.variant == "genfile" else "app")
        elif path.startswith("/app/generacionlibro/sinids"):
            body = page("<header class='navbar navbar-dark bg-primary'><span class='navbar-brand h1'>Generación de Registros Electrónicos</span></header>"
                        "<div><table class='table table-bordered'><tr><td>a</td></tr></table><table class='table table-bordered'><tr><td>b</td></tr></table>"
                        "<button class='btn btn-primary'>x</button><button class='btn btn-primary'>y</button><button class='btn btn-success'>z</button></div>")
        elif path.startswith("/app/generacionlibro/"):
            body = app_page("gen_file" if Handler.variant == "genfile" else "app")
        elif path.startswith("/app/"):
            body = app_page("app")
        elif path == "/public":
            variant = self.path.split("variant=")[1].split("&")[0] if "variant=" in self.path else "ok"
            body = page(public_html(variant), "SUNAT Operaciones en Línea")
        elif path == "/stale-shell":
            body = page(stale_app_shell("/app/libroventas/previa", ""), "SUNAT - Menú SOL")
        elif path == "/real":
            opened = "<script>['nivel1Cuerpo_60','nivel2Cuerpo_60_2','nivel3Cuerpo_60_2_1','nivel3Cuerpo_60_2_2','nivel3Cuerpo_60_2_3'].forEach(function(i){document.getElementById(i).style.display='block'})</script>" if "open=1" in self.path else ""
            body = page(real_menu_html() + opened, "SUNAT - Menú SOL")
        elif path == "/platform" or path.endswith("/cl-ti-itmenu2/MenuInternetPlataforma.htm"):
            body = page("<div id='aOpcionPlataforma'></div><div id='divBreadcrumb'></div><ul id='ulSubmenu'><li class='nivel3' id='nivel3_55_1_1' onclick=\"document.getElementById('f').src='/app/fv621'\"><span class='spanNivelDescripcion'>Declara Fácil</span></li></ul><iframe id='f' style='width:100%;height:100px'></iframe>")
        elif path.startswith("/noise/"):
            body = page("<button id='btnBuzon'>Buzón</button>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", f"{SECRETS[4]}=1; Path=/")
        self.end_headers()
        self.wfile.write(body)


def serve(variant: str):
    Handler.variant, Handler.posts = variant, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    RESULTS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name, flush=True)


class Lines:
    def __init__(self, lines): self.q = asyncio.Queue(); [self.q.put_nowait(x) for x in lines]
    async def read(self): return await self.q.get()


async def owner_expands(page_, *ids):
    for i in ids:
        await page_.evaluate("(i)=>{document.getElementById(i).style.display='block'}", i)


async def scenario(variant: str, dest: str):
    srv, base = serve(variant)
    tmp = Path(tempfile.mkdtemp())
    store = core.Store(tmp / "m3_recon_v2.json")
    browser = await PW.chromium.launch(headless=True)
    ctx = await browser.new_context()
    page_ = await ctx.new_page()
    await page_.goto(base + "/shell")
    return srv, base, tmp, store, browser, ctx, page_, core.Registry(), rec.NetworkWatcher(ctx), core.SPECS[dest]


PW = None


async def main_async() -> int:
    global PW
    async with async_playwright() as pw:
        PW = pw
        return await _run()


async def _run() -> int:
    # ---- 1. SIRE_RVIE: PRE con menu expandido + hoja con enlace javascript: -> llegada en FRAME ----
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, spec = await scenario("default", "SIRE_RVIE")
    pre_hidden = await rec.capture(ctx, reg, watcher, destination="SIRE_RVIE", kind="PRE", store=store, attempt=1)
    ev_h = core.evaluate_pre(spec, pre_hidden)
    check("PRE con el menu colapsado NO es completo (hoja oculta o ausente)", ev_h["status"] != "COMPLETE")
    await owner_expands(page_, "nivel1Cuerpo_10", "nivel2Cuerpo_10_1", "nivel3Cuerpo_10_1_1")
    pre = await rec.capture(ctx, reg, watcher, destination="SIRE_RVIE", kind="PRE", store=store, attempt=2)
    ev = core.evaluate_pre(spec, pre)
    check("PRE: hoja visible con ruta de ancestros SIRE->Registro de Ventas y accionable 'script'",
          ev["status"] == "COMPLETE" and ev["entry"]["ancestor_labels"] == ["EMPRESAS", "SIRE_ROOT", "SIRE_RVIE_REGISTRO"] and ev["entry"]["actionable_kind"] == "script"
          and ev["entry"]["leaf_level"] == 4 and ev["entry"]["header_levels"] == 3)
    store.add(pre_hidden); store.add(pre)
    check("repetir un checkpoint marca el anterior SUPERSEDED sin borrarlo",
          sum(1 for c in store.checkpoints if c["kind"] == "PRE") == 2 and store.active("SIRE_RVIE", "PRE")["checkpoint_id"] == pre["checkpoint_id"])
    # el propietario hace el clic (la herramienta NO)
    await page_.evaluate("document.querySelector('#nivel4_10_1_1_1 a').click()")
    await page_.wait_for_function("document.getElementById('iframeApplication').contentDocument && document.getElementById('iframeApplication').contentDocument.getElementById('btn-aceptar')")
    arr = await rec.capture(ctx, reg, watcher, destination="SIRE_RVIE", kind="ARRIVAL", store=store, attempt=1, linked_pre=pre["checkpoint_id"])
    arr["same_journey_confirmed"] = True
    store.add(arr)
    ea = core.evaluate_arrival(spec, store.active("SIRE_RVIE", "PRE"), arr)
    check("LLEGADA en FRAME vinculada al PRE: marcadores nuevos, host/ruta, sin ruido", ea["status"] == "COMPLETE" and ea["arrival"]["kind"] == "FRAME"
          and "btn-aceptar" in ea["arrival"]["discriminating_ids"] and ea["arrival"]["path_pattern"].startswith("/app/libroventas/"))
    check("frames de ruido (campana con btnBuzon, reloj) no son candidatos", all("noise" not in (ea["arrival"] or {}).get("path_pattern", "") for _ in [0]))
    check("sin peticiones no-GET => riesgo de efectos NONE_OBSERVED", ea["effects"]["side_effect_risk"] == "NONE_OBSERVED")
    unconfirmed = dict(arr, same_journey_confirmed=None)
    check("sin confirmacion del propietario el vinculo NO es completo", core.evaluate_arrival(spec, pre, unconfirmed)["status"] == "PARTIAL")
    # reanudacion: nuevo Store sobre el mismo archivo conserva todo
    store2 = core.Store(tmp / "m3_recon_v2.json")
    check("reanudar conserva los checkpoints previos", len(store2.checkpoints) == len(store.checkpoints) and store2.active("SIRE_RVIE", "ARRIVAL") is not None)
    text = (tmp / "m3_recon_v2.json").read_text(encoding="utf-8")
    leaks = [s for s in SECRETS if s in text]
    check(f"SANITIZACION: ningun secreto sintetico en el archivo ({leaks or 'ninguno'})", not leaks)
    check("SANITIZACION: sin HTML crudo, sin '?' ni '@' ni rachas de 5+ digitos", not re.search(r"<[a-z]+[ >]|\?|@|\d{5,}", text.replace("2026-10-12", "")))
    check("el texto de menu no etiquetado solo existe como huella de 12 letras", re.search(r'"digest": "[g-v]{12}"', text) is not None and "Mi menu" not in text)
    await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 2. DECLARACIONES_Y_PAGOS: nodo duplicado dentro del li + llegada en NUEVA ventana ----
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, _spec_unused = await scenario("default", "DECLARACIONES_Y_PAGOS")
    spec = core.DestSpec("DUP", "nodo duplicado en un li", ("DECLARACIONES_Y_PAGOS",), "", "", allow_header_target=True)  # destino de menu generico (sin ruta esperada)
    await owner_expands(page_, "nivel1Cuerpo_10", "nivel2Cuerpo_10_2")
    pre = await rec.capture(ctx, reg, watcher, destination="DECLARACIONES_Y_PAGOS", kind="PRE", store=store, attempt=1)
    ev = core.evaluate_pre(spec, pre)
    leaf = [n for _w, f in core.iter_frames(pre) for n in (f["view"].get("menu_nodes") or []) if n["label"] == "DECLARACIONES_Y_PAGOS"]
    check("nodos duplicados dentro del li: se detectan (dup_count=2) y se resuelve por visibilidad, no por texto", len(leaf) == 2 and leaf[0]["dup_count"] == 2
          and ev["status"] == "COMPLETE" and ev["entry"]["resolved_by"] == "hierarchy+visibility" and ev["entry"]["duplicate_nodes_in_li"] == 2)
    store.add(pre)
    async with ctx.expect_page() as popup_info:
        await page_.evaluate("document.querySelector('#nivel3_86_2_1').click()")
    popup = await popup_info.value
    await popup.wait_for_load_state()
    arr = await rec.capture(ctx, reg, watcher, destination="DECLARACIONES_Y_PAGOS", kind="ARRIVAL", store=store, attempt=1, linked_pre=pre["checkpoint_id"])
    arr["same_journey_confirmed"] = True
    ea = core.evaluate_arrival(spec, pre, arr)
    win = [w for w in arr["windows"] if w["opener"]]
    check("LLEGADA en NUEVA Page (popup) con opener asociado a la ventana del PRE", ea["status"] == "COMPLETE" and ea["arrival"]["kind"] == "NEW_PAGE" and win and win[0]["opener"] == pre["windows"][0]["window_id"])
    check("la ventana nueva se identifica por id tecnico (W2), no por titulo", ea["arrival"]["window"] == "W2")
    await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 3. DECLARA_FACIL dentro de la plataforma (PRE en la ventana nueva) ----
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, spec = await scenario("default", "DECLARA_FACIL")
    plat = await ctx.new_page(); await plat.goto(base + "/platform")
    pre = await rec.capture(ctx, reg, watcher, destination="DECLARA_FACIL", kind="PRE", store=store, attempt=1)
    ev = core.evaluate_pre(spec, pre)
    check("PRE de Declara Facil dentro de la plataforma (ventana propia, hoja nivel3 visible)", ev["status"] == "COMPLETE" and ev["entry"]["leaf_level"] == 3)
    store.add(pre)
    await plat.evaluate("document.getElementById('nivel3_55_1_1').click()")
    await plat.wait_for_function("document.getElementById('f').contentDocument && document.getElementById('f').contentDocument.getElementById('btn-aceptar')")
    arr = await rec.capture(ctx, reg, watcher, destination="DECLARA_FACIL", kind="ARRIVAL", store=store, attempt=1, linked_pre=pre["checkpoint_id"])
    arr["same_journey_confirmed"] = True
    ea = core.evaluate_arrival(spec, pre, arr)
    check("LLEGADA de Declara Facil: frame nuevo dentro de la misma ventana de la plataforma", ea["status"] == "COMPLETE" and ea["arrival"]["kind"] == "FRAME" and ea["arrival"]["path_pattern"] == "/app/{id}")  # el segmento con 3+ digitos se normaliza (conservador)
    # dos candidatas -> ambigua hasta que el propietario elige con V
    both = dict(arr)
    extra = json.loads(json.dumps(arr["windows"][0]["frames"][0]))
    extra["frame_id"], extra["is_main"], extra["url"] = "F99", False, {"scheme": "http", "host": "127.0.0.1", "path": "/app/otro"}
    extra["view"]["nodes"] = [{"tag": "div", "id": "otro-1"}, {"tag": "div", "id": "otro-2"}]
    both["windows"] = [dict(arr["windows"][0], frames=arr["windows"][0]["frames"] + [extra])] + arr["windows"][1:]
    amb = core.evaluate_arrival(spec, pre, both)
    check("varias candidatas => AMBIGUA hasta elegir ventana/frame por id tecnico", amb["status"] == "PARTIAL" and any("candidatas" in r for r in amb["reasons"]))
    both["observed"] = {"window": ea["arrival"]["window"], "frame": ea["arrival"]["frame"]}
    check("la eleccion explicita (V) resuelve la ambiguedad", core.evaluate_arrival(spec, pre, both)["status"] == "COMPLETE")
    await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 4. Generacion de Registros: apertura con POST o con input file => MANUAL_ONLY ----
    for variant, expect in (("genpost", "MANUAL_ONLY"), ("genfile", "MANUAL_ONLY"), ("default", "OPEN_CANDIDATE_REQUIRES_REVIEW")):
        srv, base, tmp, store, browser, ctx, page_, reg, watcher, spec = await scenario(variant, "SIRE_GENERACION_REGISTROS")
        await owner_expands(page_, "nivel1Cuerpo_10", "nivel2Cuerpo_10_1")
        pre = await rec.capture(ctx, reg, watcher, destination="SIRE_GENERACION_REGISTROS", kind="PRE", store=store, attempt=1)
        store.add(pre)
        await page_.evaluate("document.querySelector('#nivel3_10_1_3 a').click()")
        await page_.wait_for_function("document.getElementById('iframeApplication').contentDocument && document.getElementById('iframeApplication').contentDocument.getElementById('btn-aceptar')")
        await asyncio.sleep(0.4)
        arr = await rec.capture(ctx, reg, watcher, destination="SIRE_GENERACION_REGISTROS", kind="ARRIVAL", store=store, attempt=1, linked_pre=pre["checkpoint_id"])
        arr["same_journey_confirmed"], arr["owner_attests_view_only"] = True, True
        ea = core.evaluate_arrival(spec, pre, arr)
        check(f"Generacion ({variant}): decision de automatizacion = {expect}", core.generation_decision(spec, ea, arr) == expect)
        if variant == "genpost":
            check("Generacion: la peticion POST se registra solo como metodo+ruta normalizada", ea["effects"]["non_get"] and ea["effects"]["non_get"][0]["path"] == "/api/accion")
        arr["owner_attests_view_only"] = False
        if variant == "default":
            check("sin atestacion del propietario la apertura queda MANUAL_ONLY", core.generation_decision(spec, ea, arr) == "MANUAL_ONLY")
        await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 4b. flujo GUIADO completo (comandos del propietario) con repeticion de un solo checkpoint y reanudacion ----
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, spec = await scenario("default", "SIRE_RVIE")
    out: list[str] = []
    state = {"i": 0}

    async def expand():
        await owner_expands(page_, "nivel1Cuerpo_10", "nivel2Cuerpo_10_1", "nivel3Cuerpo_10_1_1")

    async def click_leaf():
        await page_.evaluate("document.querySelector('#nivel4_10_1_1_1 a').click()")
        await page_.wait_for_function("document.getElementById('iframeApplication').contentDocument && document.getElementById('iframeApplication').contentDocument.getElementById('btn-aceptar')")

    steps = [("1", None), ("P", None), ("R", expand), ("L", click_leaf), ("S", None), ("M", None), ("Q", None)]

    async def read_line():
        cmd, action = steps[state["i"]]
        state["i"] += 1
        if action:
            await action()
        return cmd

    cfg = v1.ReconConfig(login_timeout_s=20, poll_s=0.1, settle_s=0.1)
    rc = await rec.run_guided(ctx, cfg, store, {}, read_line, say=out.append)
    text = " | ".join(out)
    check("guiado: PRE con menu colapsado se registra como PARCIAL y explica que falta", "registrado como PARCIAL" in text and "OCULTA" in text)
    own = [c for c in store.checkpoints if c["kind"] == "PRE" and c["destination"] == "SIRE_RVIE" and not c.get("reuse_of")]
    check("guiado: R repite SOLO el checkpoint defectuoso (PRE), el parcial queda SUPERSEDED", len(own) == 2 and sum(1 for c in own if c["status"] == "SUPERSEDED") == 1
          and all(c["destination"] != "SIRE_RVIE" for c in store.checkpoints if c.get("reuse_of")))
    check("guiado: la llegada queda vinculada al PRE activo y confirmada", "registrada y vinculada" in text and store.active("SIRE_RVIE", "ARRIVAL")["linked_pre"] == store.active("SIRE_RVIE", "PRE")["checkpoint_id"])
    check("guiado: la matriz final marca SIRE_RVIE como COMPLETE y los demas faltantes", core.matrix(store, {})["destinations"]["SIRE_RVIE"]["overall"] == "COMPLETE"
          and core.matrix(store, {})["destinations"]["SIRE_RCE"]["overall"] == "MISSING" and rc == "KEEP")
    check("guiado: ninguna instruccion pide credenciales ni clics a la herramienta", "contraseña" not in text.lower() and "clave" not in text.lower())
    cands = core.candidate_contracts(core.matrix(store, {}))
    check("candidato de contrato SIN habilitar (enable=False) con presupuesto derivado = niveles + 1", cands["SIRE_RVIE"]["enable"] is False and cands["SIRE_RVIE"]["derived_max_clicks"] == 4)
    await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 4c. MENU REAL de SOL (sin <a>/onclick), cabecera+hoja homonimas, varias ventanas, V para el PRE, PRE compartido ----
    REAL_ALL = ["nivel1Cuerpo_60", "nivel2Cuerpo_60_2", "nivel3Cuerpo_60_2_1", "nivel3Cuerpo_60_2_2", "nivel3Cuerpo_60_2_3"]
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, spec = await scenario("default", "SIRE_RVIE")
    await page_.goto(base + "/real")
    await owner_expands(page_, *REAL_ALL)
    pre = await rec.capture(ctx, reg, watcher, destination="SIRE_RVIE", kind="PRE", store=store, attempt=1)
    ev = core.evaluate_pre(spec, pre)
    check("MENU REAL sin <a>/onclick: RVIE visible => PRE COMPLETO (el objetivo es el rotulo visible, la llegada lo confirma)",
          ev["status"] == "COMPLETE" and ev["entry"]["click_target"] == "label-span" and ev["entry"]["actionable_kind"] == "none"
          and ev["entry"]["ancestor_labels"] == ["SIRE_ROOT", "SIRE_RVIE_REGISTRO"] and ev["entry"]["notes"])
    gen = core.evaluate_pre(core.SPECS["SIRE_GENERACION_REGISTROS"], pre)
    check("cabecera y hoja con el mismo rotulo: se elige la HOJA (nivel4), no la cabecera", gen["status"] == "COMPLETE" and gen["entry"]["leaf_li_id"] == "nivel4_60_2_3_1_1")
    store.add(pre)
    added = store.derive_shared_pre()
    check("la MISMA captura evidencia RCE y Generacion (PRE reutilizado, sin duplicar el archivo)", set(added) == {"SIRE_RCE", "SIRE_GENERACION_REGISTROS"}
          and core.evaluate_pre(core.SPECS["SIRE_RCE"], store.pre_for("SIRE_RCE"))["status"] == "COMPLETE" and len([c for c in store.checkpoints if c["windows"]]) == 1)
    check("DYP/Declara Facil NO se dan por evidenciados con esa captura", "DECLARACIONES_Y_PAGOS" not in added and "DECLARA_FACIL" not in added)
    await ctx.close(); await browser.close(); srv.shutdown()

    # varias ventanas con el mismo menu expandido => ambiguo hasta fijar ventana/frame con V (flujo guiado)
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, spec = await scenario("default", "SIRE_RVIE")
    await page_.goto(base + "/real")
    second = await ctx.new_page()
    await second.goto(base + "/real")
    await owner_expands(page_, *REAL_ALL)
    await owner_expands(second, *REAL_ALL)
    amb = await rec.capture(ctx, reg, watcher, destination="SIRE_RVIE", kind="PRE", store=store, attempt=1)
    ea = core.evaluate_pre(spec, amb)
    check("dos ventanas con el menu visible => PRE ambiguo y pide V <ventana> <frame>", ea["status"] == "PARTIAL" and any("elija con V" in r for r in ea["reasons"]))
    out2: list[str] = []
    st2 = {"i": 0}

    async def read2():
        i = st2["i"]
        st2["i"] += 1
        if i == 0: return "1"
        if i == 1: return "P"
        if i == 2: return "V"
        if i == 3:
            ids = re.findall(r"(W\d+)/(F\d+)\s+127\.0\.0\.1/real\s+menú=\d+\s+visibles=(\d+)", "\n".join(out2))
            w, f, _ = [x for x in ids if int(x[2]) > 0][-1]
            return f"V {w} {f}"
        if i == 4: return "P"
        return "Q"

    cfg2 = v1.ReconConfig(login_timeout_s=20, poll_s=0.1, settle_s=0.1)
    mode = await rec.run_guided(ctx, cfg2, store, {}, read2, say=out2.append)
    joined = " | ".join(out2)
    check("guiado: P ambiguo => V lista frames con identificadores tecnicos => V <ventana> <frame> => P completo",
          "PARCIAL" in joined and "Contexto fijado" in joined and "registrado en W" in joined and mode == "KEEP")
    final = store.active("SIRE_RVIE", "PRE")
    check("el PRE final guarda la seleccion `observed` y los checkpoints previos se CONSERVAN (superseded)", final.get("observed") and final["status"] == "ACTIVE"
          and any(c["status"] == "SUPERSEDED" for c in store.checkpoints))
    await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 4d. sesion recuperable: el navegador sobrevive a la herramienta y -Attach la recupera SIN volver a autenticarse ----
    srv, base = serve("default")
    tmp = Path(tempfile.mkdtemp())
    browser, context, proc, port = await rec.launch_or_attach(PW, tmp, None, base + "/shell", headless=True)
    pg = context.pages[0] if context.pages else await context.new_page()
    await pg.goto(base + "/shell")
    await pg.evaluate("sessionStorage.setItem('sesion','autenticada-sintetica')")
    await rec.finish_browser(browser, proc, port, "KEEP", say=lambda *_a: None)
    check("Q conserva el navegador: el depurador local sigue vivo tras terminar la herramienta", rec._cdp_alive(port))
    async with async_playwright() as pw2:  # otro proceso de la herramienta (otro driver de Playwright), como al ejecutar -Attach
        b2, c2, p2, port2 = await rec.launch_or_attach(pw2, tmp, port, None)
        pages2 = c2.pages
        kept = await pages2[0].evaluate("sessionStorage.getItem('sesion')") if pages2 else None
        attached_ok = port2 == port and p2 is None and kept == "autenticada-sintetica"
        await rec.finish_browser(b2, None, port, "CLOSE", say=lambda *_a: None)  # recuperada: se cierra SOLO por CDP (sin PID)
    check("-Attach recupera la MISMA sesion (estado del navegador intacto, sin nueva autenticacion)", attached_ok)
    launch_src = (HERE / "m3_chromium_launch.py").read_text(encoding="utf-8")
    check("el depurador se abre SOLO en loopback (127.0.0.1), con puerto elegido por Chromium y perfil efimero propio",
          "--remote-debugging-address=127.0.0.1" in launch_src and "--remote-debugging-port={requested_port or 0}" in launch_src and "PROFILE_PREFIX" in launch_src)
    for _ in range(40):
        if not rec._cdp_alive(port):
            break
        await asyncio.sleep(0.25)
    check("X cierra el navegador recuperado SOLO por CDP (el depurador deja de responder)", not rec._cdp_alive(port))
    chromium.terminate_own(proc); chromium.cleanup_profile(proc)
    try:
        await rec.launch_or_attach(PW, tmp, port, None)
        attach_dead = False
    except RuntimeError:
        attach_dead = True
    check("-Attach sobre un navegador cerrado falla con mensaje claro (no inventa sesion)", attach_dead)
    srv.shutdown()

    # ---- 4e. ARRANQUE DE CHROMIUM: lento, ausente, terminado, sin puerto, sin respuesta, conexion fallida, puerto ocupado; cada causa por separado ----
    import hashlib
    import shutil
    import socket
    import subprocess
    import sys as _sys

    keep = Path(tempfile.mkdtemp())
    guard_store = core.Store(keep / "m3_recon_v2.json")
    guard_store.add({"record": "checkpoint", "checkpoint_id": "C001", "destination": "SIRE_RVIE", "kind": "PRE", "status": "ACTIVE", "windows": [], "linked_pre": None,
                     "same_journey_confirmed": None, "observed": None})
    before_hash = hashlib.sha256((keep / "m3_recon_v2.json").read_bytes()).hexdigest()
    tmp_root = chromium.profile_base()
    tmp_root.mkdir(parents=True, exist_ok=True)

    def profiles_alive():
        return [d for d in tmp_root.glob(chromium.PROFILE_PREFIX + "*") if d.is_dir()]

    start_profiles = set(profiles_alive())

    async def expect_failure(code, **kw):
        try:
            await rec.launch_or_attach(PW, keep, None, None, say=lambda *_a: None, **kw)
        except chromium.LaunchError as exc:
            return exc
        return None

    # lento: el navegador se SUSPENDE 20 s justo tras arrancar (mas que los 15 s de la version anterior) y debe arrancar igual, con el puerto REAL
    if os.name == "nt":
        import ctypes
        import threading as _th

        def suspend_for(seconds):
            def hook(launched):
                k32, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
                k32.OpenProcess.restype = ctypes.c_void_p
                h = k32.OpenProcess(0x0800, False, launched.proc.pid)
                nt.NtSuspendProcess(ctypes.c_void_p(h))
                _th.Timer(seconds, lambda: (nt.NtResumeProcess(ctypes.c_void_p(h)), k32.CloseHandle(ctypes.c_void_p(h)))).start()
            return hook

        t0 = time.monotonic()
        notes: list[str] = []
        b_slow, c_slow, l_slow, p_slow = await rec.launch_or_attach(PW, keep, None, None, headless=True, say=notes.append, _hooks={"post_start": suspend_for(20), "progress_every": 5})
        slow_s = time.monotonic() - t0
        check(f"ARRANQUE LENTO ({slow_s:.0f} s > 15 s de la version anterior): arranca igual, informa progreso y usa el puerto REAL de Chromium",
              slow_s > 18 and any("sigue iniciando" in n for n in notes) and p_slow == l_slow.port and chromium.read_devtools_port(l_slow.profile) == p_slow)
        await rec.finish_browser(b_slow, l_slow, p_slow, "CLOSE", say=lambda *_a: None)

    try:
        chromium.start_chromium(keep / "no-existe" / "chrome.exe")
        missing = None
    except chromium.LaunchError as e:
        missing = e
    check("ejecutable ausente => EXE_MISSING con instruccion de reparacion", missing is not None and missing.code == chromium.EXE_MISSING and "playwright install" in missing.render())

    code = "import sys; sys.stderr.write('ERROR: boom C:\\\\Users\\\\Luis\\\\x cookie=abc123 token=Zq9 https://a.b/c?d=1 99999999999\\n'); sys.exit(3)"
    exc = await expect_failure(chromium.PROCESS_EXITED, _hooks={"argv_override": [_sys.executable, "-c", code, "{profile}"]}, deadline_s=20)
    rendered = exc.render() if exc else ""
    check("proceso terminado => PROCESS_EXITED al instante (no espera el plazo) con stderr SANEADO (sin ruta, cookie, token, URL ni numeros largos)",
          exc is not None and exc.code == chromium.PROCESS_EXITED and exc.elapsed < 10 and "boom" in rendered
          and not any(x in rendered for x in ("Luis", "abc123", "Zq9", "a.b/c", "99999999999", "C:\\")))

    sleeper = "import time,sys; time.sleep(60)  # {profile}"
    t0 = time.monotonic()
    exc = await expect_failure(chromium.NO_DEBUGGER_PORT, _hooks={"argv_override": [_sys.executable, "-c", sleeper]}, deadline_s=3)
    check("vivo pero sin puerto de depurador => NO_DEBUGGER_PORT y SOLO se termina el proceso propio",
          exc is not None and exc.code == chromium.NO_DEBUGGER_PORT and time.monotonic() - t0 < 15)

    idle = socket.socket(); idle.bind(("127.0.0.1", 0)); dead_port = idle.getsockname()[1]; idle.close()
    announce = f"import pathlib,time; pathlib.Path(r'{{profile}}', 'DevToolsActivePort').write_text('{dead_port}\\n/devtools/browser/x'); time.sleep(60)"
    exc = await expect_failure(chromium.CDP_NOT_RESPONDING, _hooks={"argv_override": [_sys.executable, "-c", announce]}, deadline_s=15)
    check(f"puerto anunciado que no responde => CDP_NOT_RESPONDING ({getattr(exc, 'code', None)} {getattr(exc, 'detail', '')})", exc is not None and exc.code == chromium.CDP_NOT_RESPONDING and str(dead_port) in exc.detail)

    import http.server

    class BadCdp(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):  # noqa: N802
            body = b'{"Browser":"X","webSocketDebuggerUrl":"ws://127.0.0.1:1/devtools/browser/none"}'
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    bad = http.server.ThreadingHTTPServer(("127.0.0.1", 0), BadCdp)
    _th2 = __import__("threading").Thread(target=bad.serve_forever, daemon=True); _th2.start()
    liar = f"import pathlib,time; pathlib.Path(r'{{profile}}', 'DevToolsActivePort').write_text('{bad.server_address[1]}\\n/x'); time.sleep(60)"
    exc = await expect_failure(chromium.CDP_CONNECT_FAILED, _hooks={"argv_override": [_sys.executable, "-c", liar], "connect_timeout_s": 2}, deadline_s=30)
    check(f"HTTP responde pero la conexion falla => CDP_CONNECT_FAILED (distinto de 'no abrio el depurador'; obtenido {getattr(exc, 'code', None)})", exc is not None and exc.code == chromium.CDP_CONNECT_FAILED)
    bad.shutdown()

    holder = socket.socket(); holder.bind(("127.0.0.1", 0)); busy = holder.getsockname()[1]
    exc = await expect_failure(chromium.PORT_BUSY, _hooks={"requested_port": busy}, headless=True)
    holder.close()
    check("puerto pedido ocupado => PORT_BUSY (la comprobacion usa bind, no connect)", exc is not None and exc.code == chromium.PORT_BUSY)

    try:
        chromium.load_attach_port(keep, 59999)
        attach_msg = None
    except chromium.LaunchError as e:
        attach_msg = e
    check("-Attach/-Resume sin navegador => ATTACH_UNAVAILABLE con explicacion (no inventa sesion)", attach_msg is not None and attach_msg.code == chromium.ATTACH_UNAVAILABLE)

    # CAUSA RAIZ del fallo tras reiniciar: perfil sin entrada para el usuario => Chromium sale a los ~0,5 s (codigo 21, ProcessSingleton "Acceso denegado")
    t0 = time.monotonic()
    exc = await expect_failure(chromium.PROFILE_ACCESS_DENIED, _hooks={"deny_acl": True}, headless=True, deadline_s=60)
    check(f"perfil inaccesible (ACL de otro contexto) => PROFILE_ACCESS_DENIED en {time.monotonic() - t0:.1f} s, NO tras un plazo de 15-120 s, con la accion a seguir",
          exc is not None and exc.code == chromium.PROFILE_ACCESS_DENIED and time.monotonic() - t0 < 15 and "NORMAL" in exc.render() and exc.elapsed < 10)
    prof_probe = chromium.create_profile_dir()
    acl = subprocess.run(["icacls", str(prof_probe)], capture_output=True, text=True).stdout
    sid = chromium.current_user_sid()
    legacy = Path(tempfile.mkdtemp(prefix="m3-acl-legacy-"))
    legacy_acl = subprocess.run(["icacls", str(legacy)], capture_output=True, text=True).stdout.lower()
    user = os.environ.get("USERNAME", "").lower()
    check("el perfil nuevo tiene entrada EXPLICITA para el usuario; el de tempfile.mkdtemp (Python 3.13) NO la tiene (por eso fallaba)",
          prof_probe.parent == chromium.profile_base() and sid is not None and user in acl.lower() and user not in legacy_acl)
    shutil.rmtree(legacy, ignore_errors=True)
    shutil.rmtree(prof_probe, ignore_errors=True)
    check("el modulo de arranque no usa tempfile.mkdtemp para el perfil", "mkdtemp(" not in "\n".join(l for l in (HERE / "m3_chromium_launch.py").read_text(encoding="utf-8").splitlines() if not l.strip().startswith(("#", '"""', "*")) and "Python 3.13" not in l and "`tempfile.mkdtemp`" not in l))

    lines = chromium.sanitize_stderr("[1:2:3] ERROR: bind() failed C:\\Users\\Luis\\p cookie=zzz\nINFO normal\nFATAL: x token=abc")
    check("sanitize_stderr solo deja errores, sin rutas, secretos ni URLs", len(lines) == 2 and not any(x in " ".join(lines) for x in ("Luis", "zzz", "abc", "C:\\")))
    check("los fallos de arranque NO tocaron los checkpoints previos (hash identico)", hashlib.sha256((keep / "m3_recon_v2.json").read_bytes()).hexdigest() == before_hash)
    leftovers = set(profiles_alive()) - start_profiles
    check(f"los fallos limpiaron SOLO sus perfiles efimeros y no dejaron procesos propios ({len(leftovers)} perfiles sobrantes)", not leftovers)

    # ---- 4f. TARJETA PUBLICA «Mis Declaraciones y Pagos» (entrada ANTES del login) -> plataforma MenuInternetPlataforma.htm en la misma ventana ----
    dyp = core.SPECS["DECLARACIONES_Y_PAGOS"]
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, _s = await scenario("default", "DECLARACIONES_Y_PAGOS")
    await page_.goto(base + "/public")
    pre = await rec.capture(ctx, reg, watcher, destination="DECLARACIONES_Y_PAGOS", kind="PRE", store=store, attempt=1)
    ev = core.evaluate_pre(dyp, pre)
    entry = ev["entry"] or {}
    check("PUBLICO: la tarjeta «Mis Declaraciones y Pagos» con su INGRESAR visible se reconoce entre 4 tarjetas (huella JS == huella Python)",
          ev["status"] == "COMPLETE" and entry.get("kind") == "PUBLIC_CARD" and entry.get("ingresar_tag") == "span" and entry.get("heading_digest") == san.digest_of("Mis Declaraciones y Pagos"))
    store.add(pre)
    saved = json.dumps(pre, ensure_ascii=False)
    check("PUBLICO: de las otras tarjetas no se guarda texto (solo clave canonica o huella)", "Servicios generales" not in saved and "Otros" not in saved and "Empresas" not in saved.replace("EMPRESAS", ""))
    await page_.evaluate("location.href = '/cl-ti-itmenu2/MenuInternetPlataforma.htm'")  # el propietario hizo clic en INGRESAR y SUNAT abrio la plataforma
    await page_.wait_for_function("document.getElementById('aOpcionPlataforma')")
    arr = await rec.capture(ctx, reg, watcher, destination="DECLARACIONES_Y_PAGOS", kind="ARRIVAL", store=store, attempt=1, linked_pre=pre["checkpoint_id"])
    arr["same_journey_confirmed"] = True
    ea = core.evaluate_arrival(dyp, pre, arr)
    check("PUBLICO: llegada a la plataforma en la MISMA ventana (navegacion), ruta esperada, ids unicos y la hoja «Declara Fácil» visible dentro de ella",
          ea["status"] == "COMPLETE" and ea["arrival"]["kind"] == "SAME_WINDOW_NAVIGATION" and ea["arrival"]["path_pattern"].endswith("MenuInternetPlataforma.htm")
          and "DECLARA_FACIL" in ea["arrival"]["visible_menu_leaves"] and len(ea["arrival"]["positive_markers"]["ids"]) >= 3)
    wrong = copy.deepcopy(arr) if False else json.loads(json.dumps(arr))
    for w in wrong["windows"]:
        for f in w["frames"]:
            if f["is_main"]:
                f["url"]["path"] = "/cl-ti-itmenu/MenuInternet.htm"
    check("NEGATIVO: si SUNAT lleva al Menu SOL (MenuInternet.htm) y no a la plataforma, la llegada NO es Declaraciones y Pagos",
          core.evaluate_arrival(dyp, pre, wrong)["status"] == "PARTIAL" and any("no corresponde al destino esperado" in r for r in core.evaluate_arrival(dyp, pre, wrong)["reasons"]))
    for variant, why in (("none", "sin la tarjeta"), ("dup", "tarjeta repetida y visible"), ("hiddenbtn", "INGRESAR oculto"), ("hiddencard", "tarjeta oculta")):
        await page_.goto(f"{base}/public?variant={variant}")
        pv = await rec.capture(ctx, reg, watcher, destination="DECLARACIONES_Y_PAGOS", kind="PRE", store=store, attempt=2)
        check(f"NEGATIVO publico ({why}): el PRE NO es completo", core.evaluate_pre(dyp, pv)["status"] != "COMPLETE")
    await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 4g. CASO REAL RCE: otra aplicacion SIRE sigue cargada en el iframe (mismos ids de plataforma) y la llegada debe seguir siendo reconocible ----
    srv, base, tmp, store, browser, ctx, page_, reg, watcher, _s = await scenario("default", "SIRE_RCE")
    await page_.goto(base + "/stale-shell")
    await page_.wait_for_function("document.getElementById('iframeApplication').contentDocument && document.getElementById('iframeApplication').contentDocument.getElementById('btn-aceptar')")
    pre = await rec.capture(ctx, reg, watcher, destination="SIRE_RCE", kind="PRE", store=store, attempt=1)
    store.add(pre)
    stale_ids = [n["id"] for _w, f in core.iter_frames(pre) if not f["is_main"] for n in (f["view"].get("nodes") or []) if n.get("id")]
    await page_.evaluate("swap('/app/librocompras/mige/gestionlibrocompras/1.0.0/')")
    await page_.wait_for_function("document.getElementById('iframeApplication').contentWindow.location.pathname.includes('librocompras')")
    await asyncio.sleep(0.5)
    arr = await rec.capture(ctx, reg, watcher, destination="SIRE_RCE", kind="ARRIVAL", store=store, attempt=1, linked_pre=pre["checkpoint_id"])
    arr["same_journey_confirmed"] = True
    rce = core.SPECS["SIRE_RCE"]
    ea = core.evaluate_arrival(rce, pre, arr)
    check("REGRESION RCE: con la app RVIE aun cargada (mismos ids de plataforma) la llegada de RCE se reconoce por RUTA NUEVA + ids propios de esa ruta",
          "btn-aceptar" in stale_ids and ea["status"] == "COMPLETE" and ea["arrival"]["positive_markers"]["path_new"] and ea["arrival"]["positive_markers"]["shared_framework_ids"]
          and len(ea["arrival"]["positive_markers"]["ids"]) >= 3)
    check("NEGATIVO: esa misma llegada NO se acepta como RVIE (la ruta contiene librocompras)", core.evaluate_arrival(core.SPECS["SIRE_RVIE"], pre, arr)["status"] == "PARTIAL")
    # ---- 4h. CASO REAL Generacion: pantalla SIN ids; identidad = ruta nueva + 2 tablas + botones + titulo ----
    await page_.evaluate("swap('/app/generacionlibro/sinids')")
    await page_.wait_for_function("document.getElementById('iframeApplication').contentWindow.location.pathname.includes('generacionlibro')")
    await asyncio.sleep(0.5)
    arr2 = await rec.capture(ctx, reg, watcher, destination="SIRE_GENERACION_REGISTROS", kind="ARRIVAL", store=store, attempt=1, linked_pre=pre["checkpoint_id"])
    arr2["same_journey_confirmed"] = True
    eg = core.evaluate_arrival(core.SPECS["SIRE_GENERACION_REGISTROS"], pre, arr2)
    check("REGRESION Generacion: sin ids, la llegada se reconoce por ruta nueva + tablas + botones + rotulo del titulo (3 unidades)",
          eg["status"] == "COMPLETE" and eg["arrival"]["positive_markers"]["ids"] == [] and eg["arrival"]["positive_markers"]["structure"] == ["tables", "buttons"]
          and eg["arrival"]["positive_markers"]["labels"] == ["SIRE_GENERACION_REGISTROS"])
    check("NEGATIVO: la pantalla de Generacion NO se acepta como RCE", core.evaluate_arrival(rce, pre, arr2)["status"] == "PARTIAL")
    await ctx.close(); await browser.close(); srv.shutdown()

    # ---- 5. el reconocedor NO interactua: auditoria estatica ----
    src = (HERE / "m3_sol_recon.py").read_text(encoding="utf-8") + (HERE / "m3_recon_core.py").read_text(encoding="utf-8")
    forbidden = re.findall(r"\.(click|dblclick|fill|type|press|check|uncheck|select_option|set_input_files|dispatch_event|hover|tap|route|add_cookies|storage_state|screenshot|content)\(", src)
    check(f"AUDITORIA ESTATICA: cero clic/teclado/relleno/cookies/captura en el reconocedor ({forbidden or 'ninguno'})", not forbidden)
    gotos = re.findall(r"\.goto\(([^,)]*)", src + (HERE / "m3_chromium_launch.py").read_text(encoding="utf-8"))
    check(f"la unica navegacion automatica es la pagina PUBLICA de entrada (o about:blank en el diagnostico): {gotos}", sorted(g.strip() for g in gotos) == ['"about:blank"', "entry_url"] and "ENTRY_URL" in src)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"RESULT={'PASS' if not failed else 'FAIL'} ({len(RESULTS) - len(failed)}/{len(RESULTS)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
