"""M3 -- arranque ROBUSTO de Chromium para el reconocedor (Windows). Sin SUNAT, sin credenciales: solo lanza un navegador con depuracion LOCAL y espera a que
el endpoint CDP este operativo, diagnosticando CADA causa de fallo por separado.

EVIDENCIA que motivo este modulo (medida en el equipo del propietario): el arranque de Chromium varia entre 2 y 30+ s (CDP operativo en 2-6 s en caliente;
`connect_over_cdp` llego a 52 s en una prueba; hay varios productos de seguridad con analisis en tiempo real, sobre todo tras un reinicio), mientras que la
version anterior esperaba un maximo FIJO de 15 s, elegia el puerto con `connect_ex` (no detecta puertos excluidos por Windows/Hyper-V/Docker tras un
reinicio) y ante cualquier fallo solo decia «no abrio el depurador a tiempo», sin distinguir proceso terminado / sin puerto / sin respuesta / conexion fallida.

Diseno:
* El puerto lo elige CHROMIUM (`--remote-debugging-port=0`, solo 127.0.0.1) y se lee del archivo `DevToolsActivePort` de su perfil: es el puerto REAL; no hay
  conflicto con puertos ocupados o excluidos.
* Perfil EFIMERO propio (`m3-recon-profile-*` bajo la carpeta temporal): nunca el perfil personal; solo se elimina el que esta herramienta creo.
* La espera es por ESTADO (proceso vivo, archivo de puerto, HTTP, conexion) con tope amplio (120 s) y avisos de progreso; un proceso terminado se detecta al instante.
* stderr del proceso se guarda en el perfil y, si hay fallo, se muestra SANEADO (sin rutas de usuario, cookies, tokens, URLs con parametros).
* Solo se termina el PID que ESTA herramienta lanzo (verificando que su linea de comandos contiene SU perfil): jamas se matan navegadores por nombre."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_PREFIX = "m3-recon-profile-"


def profile_base() -> Path:
    """Carpeta TEMP del usuario. NO se usa la carpeta Local de AppData: el Python de la Microsoft Store REDIRIGE sus escrituras ahi a un almacen privado del
    paquete, invisible para Chromium e icacls. El perfil se crea con `Path.mkdir` (ACL heredada normal) mas una entrada explicita del usuario, NO con
    `tempfile.mkdtemp`, que en Python 3.13/Windows crea el directorio con una ACL restrictiva (solo SYSTEM, Administradores y OWNER RIGHTS, sin el usuario) que
    Chromium no puede abrir si su token no es el propietario: sale a los ~0,5 s (codigo 21, ProcessSingleton «Acceso denegado»)."""
    return Path(tempfile.gettempdir())

STARTUP_DEADLINE_S = 120.0
CONNECT_TIMEOUT_S = 90.0
CONNECT_ATTEMPTS = 2

# Causas de fallo (cerradas): permiten un mensaje concreto y pruebas por causa.
EXE_MISSING, PROCESS_EXITED, NO_DEBUGGER_PORT, CDP_NOT_RESPONDING, CDP_CONNECT_FAILED, PORT_BUSY, ATTACH_UNAVAILABLE, PROFILE_ACCESS_DENIED = (
    "EXE_MISSING", "PROCESS_EXITED", "NO_DEBUGGER_PORT", "CDP_NOT_RESPONDING", "CDP_CONNECT_FAILED", "PORT_BUSY", "ATTACH_UNAVAILABLE", "PROFILE_ACCESS_DENIED")

MESSAGES = {
    EXE_MISSING: "No se encontró el ejecutable de Chromium de Playwright. Repárelo con: .\\.venv-win-e2\\Scripts\\python.exe -m playwright install chromium",
    PROCESS_EXITED: "Chromium se cerró nada más iniciar (no es lentitud: el proceso terminó).",
    NO_DEBUGGER_PORT: "Chromium sigue vivo pero nunca abrió el puerto del depurador local.",
    CDP_NOT_RESPONDING: "Chromium anunció un puerto de depuración pero no responde en 127.0.0.1.",
    CDP_CONNECT_FAILED: "El depurador local responde, pero la conexión de Playwright falló.",
    PORT_BUSY: "El puerto de depuración pedido ya está ocupado o Windows lo tiene excluido.",
    ATTACH_UNAVAILABLE: "No hay un navegador de esta herramienta escuchando para recuperar la sesión.",
    PROFILE_ACCESS_DENIED: "Chromium se cerró al instante porque NO puede abrir su carpeta de perfil (acceso denegado). Suele ocurrir cuando PowerShell se ejecuta "
                           "como Administrador o con otra cuenta/sesión que la que creó la carpeta. Cierre esta ventana y abra una PowerShell NORMAL (sin «Ejecutar como administrador»).",
}


class LaunchError(RuntimeError):
    def __init__(self, code: str, detail: str = "", stderr_tail: list[str] | None = None, elapsed: float = 0.0) -> None:
        self.code, self.detail, self.stderr_tail, self.elapsed = code, detail, stderr_tail or [], elapsed
        super().__init__(self.render())

    def render(self) -> str:
        lines = [f"[{self.code}] {MESSAGES.get(self.code, '')}"]
        if self.detail:
            lines.append(f"   Detalle: {self.detail}")
        if self.elapsed:
            lines.append(f"   Esperé {self.elapsed:.0f} s.")
        for line in self.stderr_tail:
            lines.append(f"   chromium: {line}")
        return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------- sanitizacion de stderr
_PATH = re.compile(r"[A-Za-z]:\\[^\s'\"]+|/(?:Users|home)/[^\s'\"]+")
_URL = re.compile(r"https?://[^\s'\"]+")
_SECRETISH = re.compile(r"(?i)(token|cookie|password|passwd|secret|authorization|bearer|session)[=:]\s*\S+")
_LONG_NUM = re.compile(r"\d{6,}")


def sanitize_stderr(text: str, limit: int = 6) -> list[str]:
    """Solo lineas de error, sin rutas de usuario, URLs, pares secreto=valor ni numeros largos; 6 lineas de 200 caracteres como maximo."""
    out = []
    for raw in text.splitlines():
        if not re.search(r"(?i)error|fatal|fail|denied|denegado|cannot|unable|crash|bind", raw):
            continue
        line = _SECRETISH.sub(r"\1=<oculto>", _URL.sub("<url>", _PATH.sub("<ruta>", raw)))
        line = _LONG_NUM.sub("<n>", line).strip()[:200]
        if line and line not in out:
            out.append(line)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------------------------------------------- utilidades de red
def is_elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


_DENIED_MARKERS = ("processsingleton", "lock file can not be created", "acceso denegado", "access is denied", "failed to create a processsingleton")


def cdp_http_alive(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as resp:  # noqa: S310 -- loopback
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def port_is_bindable(port: int) -> bool:
    """Comprueba con `bind` (no con connect): detecta puertos ocupados Y excluidos por Windows."""
    import socket

    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def read_devtools_port(profile: Path) -> int | None:
    f = profile / "DevToolsActivePort"
    try:
        first = f.read_text(encoding="ascii", errors="ignore").splitlines()[0].strip()
        return int(first) if first.isdigit() and 0 < int(first) < 65536 else None
    except (OSError, IndexError, ValueError):
        return None


# --------------------------------------------------------------------------------------------------------------------- lanzamiento
CHROMIUM_FLAGS = (
    # Estables y alineadas con las de Playwright que reducen trabajo/red en el arranque (no se desactiva la sandbox del contenido ni se anade --enable-automation).
    "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    "--disable-component-update", "--disable-client-side-phishing-detection", "--disable-default-apps", "--disable-sync",
    "--disable-breakpad", "--password-store=basic",
)


@dataclass
class Launched:
    proc: subprocess.Popen
    profile: Path
    stderr_path: Path
    started: float
    port: int | None = None
    owned: bool = True  # este proceso lo lanzo ESTA herramienta (se puede terminar con seguridad)
    timings: dict = field(default_factory=dict)


def current_user_sid() -> str | None:
    if os.name != "nt":
        return None
    try:
        whoami = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"  # ruta absoluta: otros `whoami` del PATH (Git/MSYS) no aceptan /user
        out = subprocess.run([str(whoami), "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=15).stdout.strip()
        sid = out.split(",")[-1].strip().strip('"')
        return sid if sid.startswith("S-1-") else None
    except Exception:  # noqa: BLE001
        return None


def create_profile_dir() -> Path:
    """Perfil EFIMERO propio con ACL heredada normal y entrada explicita para el usuario actual (Chromium y su sandbox deben poder abrirlo)."""
    import secrets

    base = profile_base()
    base.mkdir(parents=True, exist_ok=True)
    profile = base / (PROFILE_PREFIX + secrets.token_hex(4))
    profile.mkdir()
    sid = current_user_sid()
    if os.name == "nt":
        icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
        if sid:
            subprocess.run([str(icacls), str(profile), "/grant", f"*{sid}:(OI)(CI)F", "/Q"], capture_output=True, timeout=30)
    return profile


def _deny_acl_for_tests(profile: Path) -> None:
    """SOLO PRUEBAS: reproduce el perfil creado por otro contexto de seguridad (sin entrada para el usuario)."""
    sid = current_user_sid()
    subprocess.run(["icacls", str(profile), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "/grant:r", "*S-1-5-32-544:(OI)(CI)F"], capture_output=True, timeout=30)
    if sid:
        subprocess.run(["icacls", str(profile), "/remove:g", f"*{sid}", "/Q"], capture_output=True, timeout=30)


def start_chromium(exe: str | os.PathLike, *, headless: bool = False, requested_port: int | None = None, argv_override: list[str] | None = None,
                   post_start=None, hooks_deny_acl: bool = False) -> Launched:
    """Lanza Chromium como proceso independiente (sobrevive a la herramienta). `argv_override`/`post_start` son SOLO ganchos de prueba."""
    if argv_override is None and not Path(exe).is_file():
        raise LaunchError(EXE_MISSING, "ruta: " + Path(exe).name)
    if requested_port is not None and not port_is_bindable(requested_port):
        raise LaunchError(PORT_BUSY, f"puerto {requested_port}")
    profile = create_profile_dir()
    if hooks_deny_acl:
        _deny_acl_for_tests(profile)
    stderr_path = profile.parent / (profile.name + ".stderr.log")  # fuera del perfil: Chromium puede vaciarlo/bloquearlo
    argv = [a.replace("{profile}", str(profile)) for a in argv_override] if argv_override else [str(exe), f"--remote-debugging-port={requested_port or 0}", f"--user-data-dir={profile}", *CHROMIUM_FLAGS,
                             *(["--headless=new"] if headless else []), "about:blank"]
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(stderr_path, "wb") as err:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, creationflags=flags)  # noqa: S603
    launched = Launched(proc, profile, stderr_path, time.monotonic())
    if post_start:
        post_start(launched)
    return launched


def _stderr_text(launched: Launched) -> str:
    try:
        return launched.stderr_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


async def wait_until_ready(launched: Launched, *, deadline_s: float = STARTUP_DEADLINE_S, say=print, progress_every: float = 10.0, poll: float = 0.25) -> int:
    """Espera por ESTADO hasta que el depurador responda. Devuelve el puerto REAL o lanza LaunchError con la causa concreta."""
    t0 = time.monotonic()
    next_note = progress_every
    port_seen: int | None = None
    while True:
        elapsed = time.monotonic() - t0
        code = launched.proc.poll()
        if code is not None:  # el proceso termino: no es lentitud
            text = _stderr_text(launched)
            denied = any(m in text.lower() for m in _DENIED_MARKERS)
            raise LaunchError(PROFILE_ACCESS_DENIED if denied else PROCESS_EXITED, f"código de salida {code}" + ("; PowerShell elevada" if is_elevated() else ""),
                              sanitize_stderr(text), elapsed)
        port_seen = read_devtools_port(launched.profile) or port_seen
        if port_seen and cdp_http_alive(port_seen):
            launched.port = port_seen
            launched.timings["cdp_up_s"] = round(elapsed, 1)
            return port_seen
        if elapsed >= deadline_s:
            tail = sanitize_stderr(_stderr_text(launched))
            if port_seen is None:
                raise LaunchError(NO_DEBUGGER_PORT, "no apareció el archivo de puerto del perfil", tail, elapsed)
            raise LaunchError(CDP_NOT_RESPONDING, f"puerto {port_seen}", tail, elapsed)
        if elapsed >= next_note:
            say(f"   … Chromium sigue iniciando ({elapsed:.0f} s). Tras un reinicio o con antivirus en tiempo real puede tardar; espero hasta {deadline_s:.0f} s.")
            next_note += progress_every
        await asyncio.sleep(poll)


async def connect_cdp(pw, port: int, *, timeout_s: float = CONNECT_TIMEOUT_S, attempts: int = CONNECT_ATTEMPTS, say=print):
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=timeout_s * 1000)
        except Exception as exc:  # noqa: BLE001
            last = exc
            say(f"   … la conexión con el depurador falló (intento {attempt}/{attempts}); reintento.")
            await asyncio.sleep(1.0)
    raise LaunchError(CDP_CONNECT_FAILED, type(last).__name__ if last else "", [], timeout_s * attempts)


# ------------------------------------------------------------------------------------------------------------------------ estado y cierre
STATE_FILE = "launch_state.json"


def save_state(out_dir: Path, launched: Launched) -> None:
    (out_dir / STATE_FILE).write_text(json.dumps({"port": launched.port, "pid": launched.proc.pid, "profile": launched.profile.name,
                                                  "timings": launched.timings}, indent=1), encoding="ascii")
    (out_dir / "cdp_port.txt").write_text(str(launched.port), encoding="ascii")


def load_attach_port(out_dir: Path, explicit: int | None) -> int:
    if explicit and explicit > 0:
        port = explicit
    else:
        try:
            port = int((out_dir / "cdp_port.txt").read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            raise LaunchError(ATTACH_UNAVAILABLE, "no hay cdp_port.txt: inicie la sesión sin -Resume/-Attach") from None
    if not cdp_http_alive(port):
        raise LaunchError(ATTACH_UNAVAILABLE, f"nada responde en 127.0.0.1:{port} (¿se cerró el navegador con X o se reinició Windows?)")
    return port


def _cmdline_of(pid: int) -> str:
    if os.name != "nt":
        return ""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                             capture_output=True, text=True, timeout=15).stdout
        return out.strip()
    except Exception:  # noqa: BLE001
        return ""


def terminate_own(launched: Launched) -> None:
    """Termina SOLO el proceso que esta herramienta lanzo (y su arbol), tras comprobar que su perfil es el nuestro. Nunca por nombre."""
    if not launched.owned or launched.proc.poll() is not None:
        return
    if os.name == "nt" and launched.profile.name not in _cmdline_of(launched.proc.pid):
        return  # el PID ya no es el nuestro: no se toca
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(launched.proc.pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            launched.proc.terminate()
    except Exception:  # noqa: BLE001
        pass


def _rmtree_own(p: Path) -> None:
    if os.name == "nt":
        subprocess.run(["icacls", str(p), "/reset", "/T", "/C", "/Q"], capture_output=True, timeout=60)  # restablece una ACL restrictiva propia antes de borrar
    shutil.rmtree(p, ignore_errors=True)


def cleanup_profile_by_name(name: str) -> bool:
    """Tras recuperar una sesion con -Resume y cerrarla con X: elimina el perfil efimero PROPIO registrado en `launch_state.json` (prefijo y carpeta base propia)."""
    base = profile_base().resolve()
    p = base / name
    if name.startswith(PROFILE_PREFIX) and "/" not in name and "\\" not in name and p.is_dir() and p.resolve().parent == base:
        _rmtree_own(p)
        return True
    return False


def cleanup_profile(launched: Launched) -> None:
    """Elimina el perfil efimero que creo ESTA herramienta (prefijo propio y dentro de la carpeta temporal); nada ajeno."""
    p = launched.profile
    if p.name.startswith(PROFILE_PREFIX) and p.resolve().parent == profile_base().resolve():
        _rmtree_own(p)
        try:
            launched.stderr_path.unlink()
        except OSError:
            pass
