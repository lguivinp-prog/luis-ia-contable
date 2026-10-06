"""Fase 5, Bloque 5.2, Domain E2 -- proteccion de instancia unica del
Agente Windows productivo (seccion 10 del encargo:
`ONE_PRODUCT_AGENT_PROCESS_PER_INSTALL=YES`).

Mutex nombrado de Windows (`CreateMutexW`), no un PID file -- un PID file
puede quedar obsoleto (proceso murio sin limpiarlo, o el PID fue
reasignado a otro proceso completamente distinto); un mutex nombrado lo
libera el sistema operativo automaticamente cuando el proceso propietario
termina, sin importar como termino (limpio o abrupto) -- mismo principio
ya aplicado en este repositorio para `startup_sync`'s advisory lock de
PostgreSQL (ADR-0066), adaptado aqui al equivalente correcto para un
recurso que vive en el HOST de Windows, no dentro de un contenedor
Docker (la razon por la que ADR-0066 rechazo un mutex de Windows para
`startup_sync` -- "la sincronizacion real ocurre dentro del contenedor
backend" -- no aplica aqui: el Agente SI corre nativamente en el host).

Seccion 63 del encargo: esta es una capa de conveniencia LOCAL, nunca la
unica frontera de seguridad -- las protecciones del lado del broker
(`ConnectionRegistry`/`NEW_REPLACES_OLD`/lease de secreto, Domain D/E1-B)
siguen siendo la autoridad real contra conexiones simultaneas."""

from __future__ import annotations

import platform

# Nombre determinista por instalacion -- no aleatorio, para que reinicios
# sucesivos del MISMO agente usen siempre el mismo mutex. Sin "Global\\"
# (ese prefijo exige privilegios elevados en aislamiento de sesion en
# ciertos entornos) -- el alcance de sesion actual basta para "una
# instancia por usuario de Windows conectado", que es lo que este gate
# exige.
_MUTEX_NAME = "LuisIAContable_SOL_Agent_SingleInstance_v1"

_ERROR_ALREADY_EXISTS = 183


class SingleInstanceError(Exception):
    """Ya existe otra instancia del Agente productivo corriendo para este
    usuario de Windows."""


class PlatformNotSupportedError(Exception):
    """Implementacion explicita de Windows (seccion 7 del encargo) --
    invocada fuera de Windows."""


def _is_windows() -> bool:
    return platform.system() == "Windows"


class WindowsSingleInstanceGuard:
    """Uso: `with WindowsSingleInstanceGuard(): ...` -- el mutex se
    adquiere en `__enter__` (falla con `SingleInstanceError` si ya existe)
    y se libera en `__exit__`/`close()`, incluso ante una excepcion. Si el
    proceso termina de forma abrupta sin llegar a `__exit__`, Windows
    libera el mutex automaticamente al terminar el proceso -- nunca queda
    "huerfano" bloqueando reinicios futuros (a diferencia de un PID
    file)."""

    def __init__(self, name: str = _MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._kernel32 = None

    def acquire(self) -> None:
        if not self._is_windows():
            raise PlatformNotSupportedError(
                "WindowsSingleInstanceGuard requiere Windows -- ninguna "
                "implementacion multiplataforma existe deliberadamente."
            )
        import ctypes
        import ctypes.wintypes as wintypes

        # HALLAZGO REAL (confirmado ejecutando en Windows real, seccion 74
        # del encargo): `ctypes.windll.kernel32` (sin `use_last_error=True`)
        # NO garantiza que `ctypes.get_last_error()` refleje el
        # `GetLastError()` real inmediatamente despues de la llamada -- un
        # segundo proceso pudo adquirir el MISMO mutex sin ser detectado en
        # las primeras pruebas manuales, porque `last_error` leia un valor
        # obsoleto/incorrecto. `ctypes.WinDLL(..., use_last_error=True)` es
        # la forma correcta y documentada de encadenar `GetLastError()` de
        # forma fiable a una llamada especifica.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self._name)
        last_error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(last_error)
        if last_error == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise SingleInstanceError(
                "Ya existe una instancia del Agente SOL corriendo para este "
                "usuario de Windows -- ciérrala antes de iniciar una nueva."
            )
        self._handle = handle
        self._kernel32 = kernel32

    def release(self) -> None:
        if self._handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
            self._kernel32 = None

    @staticmethod
    def _is_windows() -> bool:
        return _is_windows()

    def __enter__(self) -> WindowsSingleInstanceGuard:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
