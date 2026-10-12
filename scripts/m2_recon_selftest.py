"""Auto-prueba OFFLINE del reconocimiento M2 (ejecutar con el python nativo; NO toca SUNAT ni la red externa).

1) Fuga de secretos: una pagina sintetica con datos sensibles OBVIOS (RUC, razon social, DNI, direccion, telefono, correo,
   tokens en query/fragmento, campos ocultos, montos, cookies, localStorage/sessionStorage) pasa por el pipeline real
   (`snapshot` + `m2_recon_sanitizer.dumps`): ninguno debe aparecer; lo permitido debe sobrevivir.
2) Escenarios de cambio de Page del bloqueo observado: login en la misma Page, login en Page nueva, popup, Page original
   cerrada, dos paginas "autenticadas" simultaneas, frames, timeout, Q, etiqueta invalida, navegador cerrado.
   El "humano" de las pruebas es el propio arnes (los clics son de la PRUEBA, no del script observado).

Hosts sinteticos (todos loopback): localhost = entrada publica, 127.0.0.1 = login, 127.0.0.2 = sesion autenticada.

    .\\.venv-win-e2\\Scripts\\python.exe scripts\\m2_recon_selftest.py"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m2_recon_sanitizer as san  # noqa: E402
import m2_sol_recon as recon  # noqa: E402

_browsers = Path(__file__).resolve().parent.parent / ".pw-browsers-win-e2"
if _browsers.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_browsers))

SECRETS = [
    "20123456789", "RAZON SOCIAL FICTICIA DEL TEST", "JUAN PEREZ REPRESENTANTE", "45678912", "AV. SECRETA 123 LIMA",
    "987654321", "titular@ejemplo-falso.pe", "TOKENSECRETO999", "STATESECRETO777", "HIDDENSECRET555", "S/ 98,765.43",
    "COOKIESECRETA444", "LOCALSTORAGESECRET333", "SESSIONSTORAGESECRET222", "frag-secreto", "NOMBRE-APELLIDO-SECRETO",
    "OCULTO-EN-ATRIBUTO", "valor-de-input-secreto",
]

AUTH_A = """<!doctype html><html><head><title>SUNAT - Menú SOL</title></head>
<body>
<header><span id="hdrUser" title="RAZON SOCIAL FICTICIA DEL TEST" data-ruc="20123456789">RAZON SOCIAL FICTICIA DEL TEST</span>
<a id="lnkSalir" href="/cl-ti-itmenu/Salir.htm?token=TOKENSECRETO999#frag-secreto">Salir</a></header>
<nav id="menuSol" role="navigation" class="menu-principal ruc20123456789 nav-x">
  <ul id="ulMenu"><li id="liEmpresas"><a id="a1" href="/cl-ti-itmenu/menu/empresas/20123456789?state=STATESECRETO777">Empresas</a></li>
  <li><a id="a2" href="javascript:alert('RAZON SOCIAL FICTICIA DEL TEST')">Ficha RUC</a></li>
  <li><a href="/x/y?code=TOKENSECRETO999">Declaraciones y Pagos</a></li>
  <li><a aria-label="NOMBRE-APELLIDO-SECRETO">Datos del contribuyente</a></li>
  <li><a href="/z" title="OCULTO-EN-ATRIBUTO">Sire</a></li></ul>
</nav>
<main><table><tr><th>RUC:</th><td>20123456789</td></tr><tr><th>Dirección fiscal:</th><td>AV. SECRETA 123 LIMA</td></tr>
<tr><td>JUAN PEREZ REPRESENTANTE</td><td>DNI 45678912</td><td>Tel 987654321</td><td>titular@ejemplo-falso.pe</td><td>Deuda S/ 98,765.43</td></tr></table>
<form><input type="hidden" id="hid" name="h" value="HIDDENSECRET555"><input type="text" id="txtX" value="valor-de-input-secreto">
<input type="password" id="pwd" value="valor-de-input-secreto"></form>
<iframe id="frmContenido" src="/frame?sid=TOKENSECRETO999#frag-secreto"></iframe></main>
<script>document.cookie="sess=COOKIESECRETA444; path=/";localStorage.setItem("k","LOCALSTORAGESECRET333");sessionStorage.setItem("k","SESSIONSTORAGESECRET222");</script>
</body></html>"""

FRAME = "<!doctype html><html><head><title>Frame RAZON SOCIAL FICTICIA DEL TEST</title></head><body><div id='inner'>JUAN PEREZ REPRESENTANTE</div></body></html>"
AUTH_B = "<!doctype html><html><head><title>SUNAT - Menú SOL</title></head><body><nav id='navB' role='navigation'><a>Salir</a></nav></body></html>"
LOGIN = "<!doctype html><html><head><title>SUNAT - Menú SOL</title></head><body><form><input id='txtRuc' type='text'><input id='txtContrasena' type='password'></form></body></html>"
ENTRY_TMPL = ("<!doctype html><html><head><title>SOL</title></head><body><h2>Mis tramites y consultas</h2><a href='#'>Ingresar</a>"
              "<button id='pop' onclick=\"window.open('http://127.0.0.2:{port}/authA')\">x</button></body></html>")


def make_handler(port_holder):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            body = {"/frame": FRAME, "/authA": AUTH_A, "/authB": AUTH_B, "/login": LOGIN}.get(path)
            if body is None:
                body = ENTRY_TMPL.format(port=port_holder["port"])
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "srv=COOKIESECRETA444; Path=/")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


CFG = recon.ReconConfig(
    entry_hosts=frozenset({"localhost"}), auth_hosts=frozenset({"127.0.0.1"}), login_timeout_s=20.0, poll_s=0.1,
    status_every_s=0.5, settle_s=0.3, snapshot_timeout_s=5.0, label_timeout_s=20.0,
)


class Harness:
    def __init__(self, browser, port, tmp, cfg=CFG):
        self.browser, self.port, self.tmp, self.cfg = browser, port, tmp, cfg
        self.msgs: list[str] = []
        self.labels: asyncio.Queue = asyncio.Queue()

    def url(self, host, path):
        return f"http://{host}:{self.port}{path}"

    async def start(self):
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        await self.page.goto(self.url("localhost", "/entry"))
        self.out = Path(self.tmp) / f"out_{id(self)}.json"

        async def read_label():
            return await self.labels.get()

        self.task = asyncio.ensure_future(recon.run_recon(self.context, self.cfg, self.out, read_label, say=self.msgs.append))

    async def wait_msg(self, text, timeout=15.0):
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            if any(text in m for m in self.msgs):
                return True
            await asyncio.sleep(0.05)
        return False

    async def finish(self, timeout=20.0):
        rc = await asyncio.wait_for(self.task, timeout)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        await self.context.close()
        return rc, data


def snaps(data, label):
    return [s for s in data if s.get("label") == label]


def host_paths(snap):
    return sorted(p["url"]["path"] for p in snap["pages"])


async def main_async() -> int:
    from playwright.async_api import async_playwright

    holder = {"port": 0}
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), make_handler(holder))
    holder["port"] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    failures: list[str] = []

    def check(name, ok):
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="m2_recon_selftest_") as tmp:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)

            # ---- 1) fuga de secretos --------------------------------------------------------------------------
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(f"http://127.0.0.1:{holder['port']}/authA?token=TOKENSECRETO999&state=STATESECRETO777#frag-secreto")
            await page.wait_for_selector("#frmContenido")
            await page.wait_for_timeout(500)
            out = Path(tmp) / "leak.json"
            out.write_text(san.dumps([await recon.snapshot(ctx, "AUTHENTICATED_SHELL", CFG)]), encoding="utf-8")
            text = out.read_text(encoding="utf-8")
            await ctx.close()
            leaks = [s for s in SECRETS if s.lower() in text.lower()]
            check("SYNTHETIC_SECRET_LEAK_TEST (0 de %d valores sensibles en la salida)" % len(SECRETS), not leaks)
            if leaks:
                print("   fugas:", leaks)
            view = json.loads(text)[0]["pages"][0]["frames"][0]["view"]
            nodes = view["nodes"]
            check("nav labels survive", {"CERRAR_SESION", "EMPRESAS", "FICHA_RUC", "DECLARACIONES_Y_PAGOS", "SIRE"} <= set(view["labels_present"]))
            check("non-allowlisted text dropped", "contribuyente" not in text.lower())
            check("title allowlisted + ids/roles survive", view["title_key"] == "SUNAT_MENU_SOL" and any(n["id"] == "menuSol" for n in nodes) and any(n["role"] == "navigation" for n in nodes))
            check("input types without values", view["password_control_exists"] and any(n["input_type"] == "hidden" for n in nodes))
            check("query/fragment stripped + dynamic segment masked", "?" not in text and "token=" not in text and any(n["href_path"] == "/cl-ti-itmenu/menu/empresas/{id}" for n in nodes))
            check("script href masked", any(n["href_path"] == "[script-href]" for n in nodes))
            check("frame observed separately", len(json.loads(text)[0]["pages"][0]["frames"]) == 2)

            # ---- 2) escenarios de cambio de Page --------------------------------------------------------------
            # a) login que navega en la MISMA Page
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.wait_msg("ESPERANDO_LOGIN_MANUAL")
            await h.page.goto(h.url("127.0.0.1", "/login"))
            await h.wait_msg("LOGIN_PAGE registrado")
            await h.page.goto(h.url("127.0.0.2", "/authA"))
            ok_auth = await h.wait_msg("SESION_AUTENTICADA_DETECTADA")
            await h.wait_msg("ESPERANDO_ETIQUETA")
            await h.labels.put("FICHA_RUC")
            await h.wait_msg("[FICHA_RUC registrado]")
            await h.labels.put("Q")
            rc, data = await h.finish()
            labels = [s.get("label") for s in data[1:]]
            check("PAGE_SWITCH same-Page login navigation", ok_auth and rc == 0 and labels == ["PUBLIC_ENTRY", "LOGIN_PAGE", "FIRST_AUTHENTICATED_PAGE", "FICHA_RUC"])
            check("console prompt shows enum", any("ESPERANDO_ETIQUETA" in m and "FICHA_RUC" in m and "TRAMITES_Y_CONSULTAS" in m for m in h.msgs))

            # b) login que abre una Page NUEVA (la original queda en la entrada publica)
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.wait_msg("ESPERANDO_LOGIN_MANUAL")
            newp = await h.context.new_page()
            await newp.goto(h.url("127.0.0.2", "/authA"))
            ok_auth = await h.wait_msg("SESION_AUTENTICADA_DETECTADA")
            await h.wait_msg("ESPERANDO_ETIQUETA")
            await h.labels.put("MENU_SOL")
            await h.wait_msg("[MENU_SOL registrado]")
            await h.labels.put("Q")
            rc, data = await h.finish()
            snap = snaps(data, "MENU_SOL")[0]
            check("PAGE_SWITCH new Page after login", ok_auth and rc == 0 and snap["page_count"] == 2 and "/authA" in host_paths(snap) and any("PAGE_NUEVA" in m for m in h.msgs))

            # c) popup
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.wait_msg("ESPERANDO_LOGIN_MANUAL")
            async with h.context.expect_page() as popup_info:
                await h.page.click("#pop")  # clic de la PRUEBA (humano simulado), no del script observado
            popup = await popup_info.value
            await popup.wait_for_load_state()
            ok_auth = await h.wait_msg("SESION_AUTENTICADA_DETECTADA")
            await h.wait_msg("ESPERANDO_ETIQUETA")
            await h.labels.put("SIRE")
            await h.wait_msg("[SIRE registrado]")
            await h.labels.put("Q")
            rc, data = await h.finish()
            check("POPUP handling", ok_auth and rc == 0 and snaps(data, "SIRE")[0]["page_count"] == 2)

            # d) la Page original se CIERRA
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.wait_msg("ESPERANDO_LOGIN_MANUAL")
            newp = await h.context.new_page()
            await newp.goto(h.url("127.0.0.2", "/authA"))
            await h.wait_msg("ESPERANDO_ETIQUETA")
            await h.page.close()
            await h.wait_msg("PAGE_CERRADA")
            await h.labels.put("DECLARA_FACIL")
            await h.wait_msg("[DECLARA_FACIL registrado]")
            await h.labels.put("Q")
            rc, data = await h.finish()
            snap = snaps(data, "DECLARA_FACIL")[0]
            check("CLOSED_PAGE original closes, observation continues", rc == 0 and snap["page_count"] == 1 and host_paths(snap) == ["/authA"])

            # e) dos paginas SUNAT simultaneas
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.wait_msg("ESPERANDO_LOGIN_MANUAL")
            for path in ("/authA", "/authB"):
                p = await h.context.new_page()
                await p.goto(h.url("127.0.0.2", path))
            await h.wait_msg("ESPERANDO_ETIQUETA")
            await h.labels.put("DECLARACIONES_Y_PAGOS")
            await h.wait_msg("[DECLARACIONES_Y_PAGOS registrado]")
            await h.labels.put("Q")
            rc, data = await h.finish()
            snap = snaps(data, "DECLARACIONES_Y_PAGOS")[0]
            check("two simultaneous authenticated pages both observed", rc == 0 and {"/authA", "/authB"} <= set(host_paths(snap)))

            # f) frames: el frame principal cambia entre etiquetas y el iframe se observa
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.wait_msg("ESPERANDO_LOGIN_MANUAL")
            await h.page.goto(h.url("127.0.0.2", "/authA"))
            await h.wait_msg("ESPERANDO_ETIQUETA")
            await h.labels.put("AUTHENTICATED_SHELL")
            await h.wait_msg("[AUTHENTICATED_SHELL registrado]")
            await h.page.goto(h.url("127.0.0.2", "/authB"))  # el frame principal cambia
            await h.labels.put("TRAMITES_Y_CONSULTAS")
            await h.wait_msg("[TRAMITES_Y_CONSULTAS registrado]")
            await h.labels.put("Q")
            rc, data = await h.finish()
            first = snaps(data, "AUTHENTICATED_SHELL")[0]["pages"][0]
            second = snaps(data, "TRAMITES_Y_CONSULTAS")[0]["pages"][0]
            main_first = next(f for f in first["frames"] if f["is_main"])
            main_second = next(f for f in second["frames"] if f["is_main"])
            check("FRAME main frame change + sub-frame observed", rc == 0 and len(first["frames"]) == 2 and main_first["url"]["path"] == "/authA"
                  and main_second["url"]["path"] == "/authB" and "navB" in json.dumps(main_second["view"]["nodes"]))

            # g) timeout sin sesion
            cfg = recon.ReconConfig(**{**CFG.__dict__, "login_timeout_s": 1.5})
            h = Harness(browser, holder["port"], tmp, cfg)
            await h.start()
            rc, data = await h.finish()
            check("TIMEOUT status and exit code", rc == recon.RC_TIMEOUT and any("TIMEOUT_SIN_SESION_AUTENTICADA" in m for m in h.msgs) and any("ESPERANDO_LOGIN_MANUAL (" in m for m in h.msgs))

            # h) el usuario pulsa Q enseguida / etiqueta invalida
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.page.goto(h.url("127.0.0.2", "/authA"))
            await h.wait_msg("ESPERANDO_ETIQUETA")
            await h.labels.put("BUZON_SOL")
            await h.wait_msg("Etiqueta no válida")
            await h.labels.put("q")
            rc, data = await h.finish()
            check("Q ends cleanly + invalid label rejected (no snapshot saved)", rc == 0 and not snaps(data, "BUZON_SOL"))

            # i) el navegador se cierra mientras espera
            h = Harness(browser, holder["port"], tmp)
            await h.start()
            await h.wait_msg("ESPERANDO_LOGIN_MANUAL")
            await h.page.close()
            rc, data = await h.finish()
            check("browser closed while waiting for login", rc == recon.RC_BROWSER_CLOSED and any("NAVEGADOR_CERRADO" in m for m in h.msgs))
            await browser.close()
        server.shutdown()
        everything = "".join(p.read_text(encoding="utf-8") for p in Path(tmp).glob("*.json"))
        check("no secret in ANY scenario output", not [s for s in SECRETS if s.lower() in everything.lower()])
        check("only .json files written", {p.suffix for p in Path(tmp).iterdir()} == {".json"})

    print("RESULT=" + ("PASS" if not failures else "FAIL " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
