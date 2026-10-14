"""PORTAL LOCAL DETERMINISTA -- SOLO PARA PRUEBAS (Fase 5 / Bloque 5.2 /
Domain E3). NO es codigo de producto y NO copia HTML real de SUNAT: es un
fixture SINTETICO MINIMO que replica unicamente la ESTRUCTURA observada en
LIVE-A1 (ver BLOCK5_2_WINDOWS_AGENT.md seccion 12), con datos FICTICIOS:

- portada con 5 enlaces "Ingresar"; el correcto cuelga (contenedor hermano
  siguiente) del encabezado DOM "Mis tramites y consultas" (mayusculas por CSS);
- el login se abre en una Page NUEVA (`window.open`) en otro host;
- controles por id sin `<label>` ni `name`: txtRuc, txtUsuario, txtContrasena,
  btnAceptar; botones btnPorRuc/btnPorDni; modo RUC activo por defecto, txtRuc
  visible, txtDni oculto; `input#captcha` hidden en contenedor oculto;
- ruta del login `/v1/clientessol/<client-id>/oauth2/loginMenuSol` (dinamica).

Hosts `*.localhost` (Chromium los resuelve a loopback), un solo listener
127.0.0.1 distinguido por la cabecera Host:
  entry.localhost -> /sol.html     auth.localhost -> login / j_security_check
  menu.localhost  -> landing       evil.localhost -> host inesperado

Variantes (`set_scenario`): success, invalid, challenge, challenge_never,
unexpected_landing, frame_login, hang_entry, bad_landing, bad_landing_path, landing_with_login_form, y las de contrato:
no_heading, heading_changed, no_entry_link, no_txtRuc, dup_txtRuc,
ruc_wrong_type, ruc_wrong_maxlength, no_txtUsuario (alias changed_selector),
pwd_not_password, no_btnAceptar, dup_btnAceptar, bad_login_host,
bad_login_path, mode_inactive, mode_activation_fails."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from tests.e3_portal import m2_shell

LANDING_PATH = "/cl-ti-itmenu/MenuInternet.htm"  # observado en LIVE-B
AUTH_HOP_PATH = "/cl-ti-itmenu/AutenticaMenuInternet.htm"  # redireccion intermedia (originalUrl de Spike A)
WRONG_APP_LANDING_PATH = "/otra-app/MenuInternet.htm"
LOGIN_PATH = "/v1/clientessol/fixture-client-0001/oauth2/loginMenuSol"
WELCOME_PATH = "/v1/clientessol/fixture-client-0001/oauth2/welcome"  # transito de autenticacion posterior al envio
# M2: tambien SIRE y las acciones consecuentes dentro de la Ficha (imprimir/modificar): jamas deben solicitarse.
FORBIDDEN_PATH_PREFIXES = ("/buzon", "/declaraciones", "/pagos", "/tramites", "/download", "/sire", "/ficha/", "/contacto/")
DECOY_PATH_PREFIX = "/decoy"


class FakeSolPortal:
    def __init__(self) -> None:
        self.scenario = "success"
        self.challenge_released = threading.Event()
        self.requests: list[tuple[str, str, str]] = []  # (host, method, path)
        self.submissions: list[dict[str, str]] = []
        self.mode_clicks: list[str] = []
        self.hang_seconds = 6.0
        self.hop_delay_ms = 700  # retardo del salto del host de aterrizaje antes de redirigir al login
        self._lock = threading.Lock()
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silencioso
                pass

            def _host(self) -> str:
                return (self.headers.get("Host") or "").split(":")[0]

            def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", headers=None) -> None:
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                host, path = self._host(), self.path.split("?")[0]
                with portal._lock:
                    portal.requests.append((host, "GET", path))
                portal._route_get(self, host, path)

            def do_POST(self):  # noqa: N802
                host, path = self._host(), self.path.split("?")[0]
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
                with portal._lock:
                    portal.requests.append((host, "POST", path))
                portal._route_post(self, host, path, form)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # --- ciclo de vida ---

    def start(self) -> FakeSolPortal:
        self._thread.start()
        return self

    def stop(self) -> None:
        self.challenge_released.set()
        self._server.shutdown()
        self._server.server_close()

    def set_scenario(self, name: str) -> None:
        self.scenario = "no_txtUsuario" if name == "changed_selector" else name
        self.challenge_released.clear()

    def release_challenge(self) -> None:
        self.challenge_released.set()

    # --- URLs y auditoria ---

    def url(self, role: str, path: str) -> str:
        return f"http://{role}.localhost:{self.port}{path}"

    @property
    def landing_url(self) -> str:
        return self.url("menu", LANDING_PATH)

    def count(self, host: str, path_prefix: str, method: str | None = None) -> int:
        with self._lock:
            return sum(
                1 for h, m, p in self.requests if h == host and p.startswith(path_prefix) and (method in (None, m))
            )

    def forbidden_requests(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return [r for r in self.requests if r[2].startswith(FORBIDDEN_PATH_PREFIXES)]

    def decoy_requests(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return [r for r in self.requests if r[2].startswith(DECOY_PATH_PREFIX)]

    # --- paginas ---

    def _login_target(self) -> str:
        s = self.scenario
        if s == "bad_login_host":
            return self.url("evil", LOGIN_PATH)
        if s == "bad_login_path":
            return self.url("auth", "/otra/ruta/loginMenuSol")
        if s == "frame_login":
            return self.url("entry", "/frame_host")
        if s.startswith("m2_login_hop"):
            # El login del portal REAL pasa por un salto del host de aterrizaje (observado en vivo en el preflight): una pagina de transito
            # en `e-menu` con la ruta del menu y un titulo que aun no es el del landing, que redirige al login.
            return self.url("menu", "/otra/ruta/Salto.htm" if s == "m2_login_hop_wrong_path" else "/cl-ti-itmenu/SaltoLogin.htm")
        return self.url("auth", LOGIN_PATH)

    def _entry_html(self) -> str:
        s = self.scenario
        if s.startswith("m3_public"):
            from tests.e3_portal import m3_fixtures

            variant, _inherited = m3_fixtures.variant_of(s)
            return m3_fixtures.public_entry_html(variant or "public", f"window.open('{self._login_target()}', '_blank');")
        heading = {"heading_changed": "Mis tramites y servicios"}.get(s, "Mis tramites y consultas")
        h2 = "" if s == "no_heading" else f"<h2>{heading}</h2>"
        link = "" if s == "no_entry_link" else '<a href="javascript:openLogin()">Ingresar</a>'
        return f"""<html><head><title>SUNAT SOL Operaciones en Línea (fixture)</title>
<style>h2 {{ text-transform: uppercase; }}</style>
<script>function openLogin() {{ window.open('{self._login_target()}', '_blank'); }}</script></head><body>
<h1>Selecciona una opción para ingresar con tu clave SOL</h1>
<section>
<div><h2>Mis declaraciones y pagos</h2><div><a href="{DECOY_PATH_PREFIX}/declaraciones">Ingresar</a></div></div>
<div>{h2}<div>{link}</div></div>
<div><h3>Personas</h3><div><a href="{DECOY_PATH_PREFIX}/personas">Ingresar</a></div></div>
<div><h3>Empresas</h3><div><a href="{DECOY_PATH_PREFIX}/empresas">Ingresar</a></div></div>
<div><h3>Pagos sin clave SOL</h3><div><a href="{DECOY_PATH_PREFIX}/pagos">Ingresar</a></div></div>
</section></body></html>"""

    def _login_html(self, *, error: str = "", challenge: bool = False) -> str:
        s = self.scenario
        ruc_active = s not in {"mode_inactive", "mode_activation_fails"}
        ruc_type = "number" if s == "ruc_wrong_type" else "text"
        ruc_max = "10" if s == "ruc_wrong_maxlength" else "11"
        ruc = "" if s == "no_txtRuc" else (
            f'<input id="txtRuc" type="{ruc_type}" placeholder="RUC" maxlength="{ruc_max}" autocomplete="off"'
            f'{"" if ruc_active else " style=\'display:none\'"}>'
        )
        if s == "dup_txtRuc":
            ruc += '<input id="txtRuc" type="text" placeholder="RUC" maxlength="11" autocomplete="off">'
        user = "" if s == "no_txtUsuario" else '<input id="txtUsuario" type="text" placeholder="Usuario" maxlength="8" autocomplete="off">'
        pwd_type = "text" if s == "pwd_not_password" else "password"
        pwd = f'<input id="txtContrasena" type="{pwd_type}" placeholder="Contraseña" maxlength="12" autocomplete="off">'
        submit = "" if s == "no_btnAceptar" else '<button id="btnAceptar" type="button" onclick="doLogin()">Iniciar sesión</button>'
        if s == "dup_btnAceptar":
            submit += '<button id="btnAceptar" type="button">Iniciar sesión</button>'
        dni_style = "display:none" if ruc_active else ""
        challenge_html = ""
        if challenge:
            challenge_html = (
                "<div id='captcha-widget' class='captcha-box' style='width:300px;height:80px'><p>Verificación humana (fixture)</p></div>"
                "<script>setInterval(async()=>{const r=await fetch('/challenge_status');"
                f"if((await r.text())==='released'){{location.href='{self.landing_url}';}}}},150);</script>"
            )
        activate = "" if s == "mode_activation_fails" else "setMode('ruc');"
        return f"""<html><head><title>SUNAT - Menú SOL</title></head><body>
<h1>Acceso SOL (fixture)</h1><p id="err">{error}</p>
<div><button id="btnPorRuc" type="button" class="btn {'active ' if ruc_active else ''}btnPor" onclick="fetch('/mode_click?m=ruc');{activate}">RUC</button>
<button id="btnPorDni" type="button" class="btn {'' if ruc_active else 'active '}btnPor" onclick="fetch('/mode_click?m=dni');setMode('dni')">DNI</button></div>
<input id="txtDni" type="text" placeholder="DNI" maxlength="8" style="{dni_style}">
{ruc}{user}{pwd}{submit}
<div style="display:none"><input type="hidden" id="captcha"></div>
{challenge_html}
<script>
function setMode(m) {{
  document.getElementById('btnPorRuc').classList.toggle('active', m==='ruc');
  document.getElementById('btnPorDni').classList.toggle('active', m==='dni');
  const r=document.getElementById('txtRuc'); if(r) r.style.display = (m==='ruc')?'':'none';
  document.getElementById('txtDni').style.display = (m==='dni')?'':'none';
}}
function doLogin() {{
  const f=document.createElement('form'); f.method='post'; f.action='/j_security_check';
  const add=(n,id)=>{{const e=document.getElementById(id); const i=document.createElement('input'); i.type='hidden'; i.name=n; i.value=e?e.value:''; f.appendChild(i);}};
  add('ruc','txtRuc'); add('user','txtUsuario'); add('pass','txtContrasena'); document.body.appendChild(f); f.submit();
}}
</script></body></html>"""

    def _auth_scenario(self) -> str:
        """Escenario que gobierna el transito POSTERIOR al envio: para `m3_<variante>__<m2_auth_*|unexpected_landing>` hereda el comportamiento del M2
        (pagina «Bienvenidos a SUNAT» lenta, detenida, con popup...), de modo que la entrada PUBLICA se prueba contra las mismas transiciones reales."""
        if self.scenario.startswith("m3_"):
            from tests.e3_portal import m3_fixtures

            _variant, inherited = m3_fixtures.variant_of(self.scenario)
            if inherited.startswith("m2_auth_") or inherited == "unexpected_landing":
                return inherited
        return self.scenario

    def _welcome_html(self) -> str:
        """Pagina de transito de autenticacion POSTERIOR al envio («Bienvenidos a SUNAT»): ruta y titulo distintos del login, sin
        formulario. Variantes (`m2_auth_*`): welcome_redirect / welcome_slow (redirigen solas al menu), welcome_chain (via el salto del host
        de aterrizaje), welcome_stuck (se queda), welcome_empty (sin ningun control), welcome_challenge (widget de verificacion humana que
        el humano libera), welcome_popup (abre otra ventana)."""
        s = self._auth_scenario()
        landing = self.url("menu", AUTH_HOP_PATH)
        script = ""
        body = "<p>Bienvenidos (fixture)</p>"
        if s in {"m2_auth_welcome_redirect", "m2_auth_welcome_chain", "m2_auth_welcome_slow"}:
            delay = {"m2_auth_welcome_redirect": 800, "m2_auth_welcome_chain": 300, "m2_auth_welcome_slow": 4000}[s]
            script = f"setTimeout(function(){{location.replace({landing!r});}}, {delay});"
        elif s == "m2_auth_welcome_empty":
            body = ""
        elif s == "m2_auth_welcome_challenge":
            body = "<div id='captcha-widget' class='captcha-box' style='width:300px;height:80px'><p>Verificación humana (fixture)</p></div>"
            script = (
                "setInterval(async()=>{const r=await fetch('/challenge_status');"
                f"if((await r.text())==='released'){{location.href={landing!r};}}}},150);"
            )
        elif s == "m2_auth_welcome_script_missing":
            body = '<h1>Bienvenidos (fixture)</h1><script src="/auth_app.js"></script>'
        elif s == "m2_auth_welcome_script_unreachable":
            body = '<h1>Bienvenidos (fixture)</h1><script src="http://127.0.0.1:1/auth_app.js"></script>'
        elif s == "m2_auth_welcome_js_error":
            script = "throw new TypeError('fixture');"
        elif s == "m2_auth_welcome_hang":
            script = f"setTimeout(function(){{location.replace({self.url('menu', '/cl-ti-itmenu/Hang.htm')!r});}}, 300);"
        elif s == "m2_auth_welcome_popup":
            script = "window.open(" + repr(self.url("menu", "/aviso.html")) + ",'aviso','width=300,height=200');"
        return f"<html><head><title>Bienvenidos a SUNAT</title></head><body>{body}<script>{script}</script></body></html>"

    def _landing_html(self) -> str:
        if self.scenario.startswith(("m2_", "m3_")):
            return m2_shell.shell_html(self.scenario, self.url("ficha", m2_shell.FICHA_PATH))
        title = "Otra aplicacion" if self.scenario == "bad_landing" else "SUNAT - Menú SOL"
        login_form = (
            '<input id="txtContrasena" type="password" placeholder="Contraseña" maxlength="12">'
            if self.scenario == "landing_with_login_form" else ""
        )
        # frames hijos irrelevantes (sin hostname normal): jamas deben crear ni impedir el exito por si solos
        return f"""<html><head><title>{title}</title></head><body><h1>Menú SOL (fixture)</h1>
<ul><li><a href="/buzon">Buzón Electrónico</a></li><li><a href="/declaraciones">Mis declaraciones</a></li>
<li><a href="/pagos">Pagos</a></li></ul>{login_form}
<iframe src="about:blank" style="display:none"></iframe><iframe srcdoc="<p>x</p>" style="display:none"></iframe>
</body></html>"""

    def _post_login_target(self) -> str:
        """A donde lleva el salto posterior al login: el Menu SOL (Tramites y Consultas) o, en los escenarios publicos, la PLATAFORMA (salvo `wronglanding`)."""
        if self.scenario.startswith("m3_public") and not self.scenario.startswith("m3_public_wronglanding"):
            return self.url("menu", "/cl-ti-itmenu2/MenuInternetPlataforma.htm")
        return self.landing_url

    # --- ruteo ---

    def _route_get(self, h, host: str, path: str) -> None:
        if host == "entry.localhost" and path == "/sol.html":
            if self.scenario == "hang_entry":
                time.sleep(self.hang_seconds)
            h._send(200, self._entry_html())
        elif host == "entry.localhost" and path == "/frame_host":
            h._send(200, f"<html><head><title>x</title></head><body><iframe src='{self.url('auth', LOGIN_PATH)}'></iframe></body></html>")
        elif host == "auth.localhost" and path == WELCOME_PATH and self._auth_scenario() == "m2_auth_welcome_server_error":
            h._send(500, "<html><head><title>Error</title></head><body><h1>Error interno (fixture)</h1></body></html>")
        elif host == "auth.localhost" and path.startswith(WELCOME_PATH) and self._auth_scenario().startswith("m2_auth_"):
            h._send(200, self._welcome_html())
        elif host == "auth.localhost" and path == "/auth_app.js":
            h._send(404, "not found", "text/plain")  # recurso JS del proveedor que no carga
        elif host == "menu.localhost" and path == "/cl-ti-itmenu/Hang.htm":
            time.sleep(25.0)  # redireccion detenida: el servidor no responde
            h._send(200, "<html><head><title>x</title></head><body></body></html>")
        elif host in {"auth.localhost", "evil.localhost"} and (path.startswith("/v1/clientessol/") or path.startswith("/otra/")):
            h._send(200, self._login_html())
        elif path == "/mode_click":
            with self._lock:
                self.mode_clicks.append(h.path.split("m=")[-1])
            h._send(200, "ok", "text/plain")
        elif path == "/challenge_status":
            h._send(200, "released" if self.challenge_released.is_set() else "pending", "text/plain")
        elif host == "menu.localhost" and path == AUTH_HOP_PATH:
            h._send(302, "", headers={"Location": self._post_login_target()})
        elif host == "menu.localhost" and path == "/cl-ti-itmenu2/MenuInternetPlataforma.htm":
            from tests.e3_portal import m3_fixtures

            variant, _inherited = m3_fixtures.variant_of(self.scenario)
            h._send(200, m3_fixtures.platform_html(variant or ""))
        elif host == "menu.localhost" and path in {LANDING_PATH, WRONG_APP_LANDING_PATH}:
            h._send(200, self._landing_html())
        elif host == "ficha.localhost" and path.startswith("/ol-ti-itmoddatruc/"):
            h._send(200, m2_shell.ficha_html(self.scenario))
        elif host == "ficha.localhost" and path.startswith(("/destino-", "/otra-ruta/", "/destino-extra/", "/app/")):
            from tests.e3_portal import m3_fixtures

            variant, _inherited = m3_fixtures.variant_of(self.scenario)
            h._send(200, m3_fixtures.destination_html(variant or "", path))
        elif host == "menu.localhost" and path in {"/cl-ti-itmenu/SaltoLogin.htm", "/otra/ruta/Salto.htm"} and self.scenario.startswith("m2_login_hop"):
            s = self.scenario
            login_url = self.url("auth", LOGIN_PATH)
            if s == "m2_login_hop_shell":
                # un shell AUTENTICADO completo (marcador DOM positivo) ANTES de entregar credenciales: sesion inesperada
                h._send(200, m2_shell.shell_html("m2_success", self.url("ficha", m2_shell.FICHA_PATH)))
                return
            title = {"m2_login_hop_empty_title": "", "m2_login_hop_menu_title": "SUNAT - Menú SOL", "m2_login_hop_menu_title_never": "SUNAT - Menú SOL",
                     "m2_login_hop_popup": "SUNAT - Menú SOL"}.get(s, "Redirigiendo (fixture)")
            never = s in {"m2_login_hop_never", "m2_login_hop_wrong_path", "m2_login_hop_menu_title_never"}
            if never:
                action = ""
            elif s == "m2_login_hop_new_page":
                # el login se abre en OTRA Page mientras el transito sigue abierto: dos Pages simultaneas (ambiguo => fallo cerrado)
                action = f"setTimeout(function(){{window.open({login_url!r});}}, {self.hop_delay_ms});"
            else:
                action = f"setTimeout(function(){{location.replace({login_url!r});}}, {self.hop_delay_ms});"
            popup = "window.open('/aviso.html','aviso','width=300,height=200');" if s == "m2_login_hop_popup" else ""
            h._send(200, f"<html><head><title>{title}</title></head><body><p>Un momento (fixture)</p><script>{popup}{action}</script></body></html>")
        elif host == "menu.localhost" and path == "/aviso.html":
            h._send(200, "<html><head><title>Aviso (fixture)</title></head><body><p>Aviso informativo (fixture)</p></body></html>")
        elif host == "evil.localhost":
            h._send(200, "<html><head><title>SUNAT - Menú SOL</title></head><body>unexpected</body></html>")
        else:
            h._send(404, "not found", "text/plain")

    def _route_post(self, h, host: str, path: str, form: dict[str, str]) -> None:
        if host != "auth.localhost" or path != "/j_security_check":
            h._send(404, "not found", "text/plain")
            return
        with self._lock:
            self.submissions.append(
                {"mode": "ruc", "ruc": form.get("ruc", ""), "user": form.get("user", ""), "pass": form.get("pass", "")}
            )
        if self.scenario == "invalid":
            h._send(200, self._login_html(error="Credenciales inválidas (fixture)"))
        elif self.scenario in {"challenge", "challenge_never"}:
            if self.scenario == "challenge_never":
                self.challenge_released.clear()
            h._send(200, self._login_html(challenge=True))
        elif self._auth_scenario() == "m2_auth_welcome_drop":
            import socket

            h.close_connection = True
            h.connection.shutdown(socket.SHUT_RDWR)  # interrupcion de conexion durante el envio
        elif self._auth_scenario() == "m2_auth_welcome_sensitive":
            # ruta, query y fragmento con identificadores SINTETICOS sensibles: nada de esto puede llegar a la traza
            h._send(302, "", headers={"Location": self.url("auth", WELCOME_PATH) + "/20123456786/JUAN-PEREZ?code=CODE-SECRET-123&state=STATE-SECRET-456#access_token=TOKEN-SECRET-789"})
        elif self._auth_scenario().startswith("m2_auth_"):
            h._send(302, "", headers={"Location": self.url("auth", WELCOME_PATH)})  # el envio llega a la pagina «Bienvenidos a SUNAT»
        elif self._auth_scenario() == "unexpected_landing":
            h._send(302, "", headers={"Location": self.url("evil", "/x")})
        else:
            target = self.url("menu", WRONG_APP_LANDING_PATH if self.scenario == "bad_landing_path" else AUTH_HOP_PATH)
            h._send(302, "", headers={"Location": target})
