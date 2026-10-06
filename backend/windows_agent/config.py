"""Fase 5, Bloque 5.2, Domain E2 -- directorio de datos y configuracion NO
secreta del Agente Windows (secciones 11/12/22 del encargo).

Directorio de datos: `%LOCALAPPDATA%\\LuisIAContable\\SolAgent` --
convencion estandar de Windows para datos de aplicacion especificos del
usuario que NO deben sincronizarse por roaming (`LOCALAPPDATA`, nunca
`APPDATA` que si hace roaming -- la identidad TLS del agente esta atada
por DPAPI al perfil/maquina de este usuario especifico, sincronizarla por
roaming la haria inutilizable en otra maquina de todas formas). Nunca el
arbol del repositorio, Descargas, Escritorio, ni el directorio de trabajo
actual."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

_APP_DIR_NAME = "LuisIAContable"
_AGENT_DIR_NAME = "SolAgent"
_CONFIG_FILENAME = "agent_config.json"

# Lista blanca EXACTA de contenido persistente permitido (seccion 12 del
# encargo) -- cualquier campo fuera de esta lista en un JSON de
# configuracion cargado se rechaza (falla cerrado, nunca se ignora en
# silencio un campo desconocido que pudiera ser un secreto colado por
# error).
_ALLOWED_CONFIG_FIELDS = frozenset(
    {
        "broker_host",
        "pairing_port",
        "operational_port",
        "protocol_version",
        "connect_timeout_seconds",
        "read_timeout_seconds",
    }
)

# Seccion 23 del encargo: el host del broker NUNCA es configurable a un
# valor remoto -- unicamente estos dos literales, exactamente igual que
# `BrokerServer._ALLOWED_HOSTS` en el lado del broker (Domain D). Este
# conjunto es la autoridad del LADO DEL AGENTE; el broker sigue validando
# el suyo propio de forma independiente (defensa en profundidad, nunca
# una unica barrera).
ALLOWED_BROKER_HOSTS = frozenset({"127.0.0.1", "localhost"})


class ConfigError(Exception):
    """Nunca incluye el contenido crudo del archivo invalido en el
    mensaje (podria contener un intento de inyeccion de secreto)."""


class RemoteBrokerHostRejectedError(ConfigError):
    """`REMOTE_BROKER_CONFIGURATION_ALLOWED=NO` -- ver seccion 23 del
    encargo."""


@dataclass(frozen=True)
class AgentConfig:
    """SOLO configuracion no secreta (seccion 22 del encargo) -- ningun
    campo de este dataclass puede ser una credencial SOL, un
    connection_id, ni material de identidad. La identidad en si vive por
    separado en `WindowsDpapiIdentityStore` (`identity.py`), nunca aqui."""

    broker_host: str = "127.0.0.1"
    pairing_port: int = 8995
    operational_port: int = 8996
    protocol_version: int = 1
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.broker_host not in ALLOWED_BROKER_HOSTS:
            raise RemoteBrokerHostRejectedError(
                f"broker_host debe ser uno de {sorted(ALLOWED_BROKER_HOSTS)} -- "
                "no se admite un broker remoto en v1."
            )

    @property
    def pairing_base_url(self) -> str:
        return f"https://{self.broker_host}:{self.pairing_port}"

    @property
    def operational_base_url(self) -> str:
        return f"https://{self.broker_host}:{self.operational_port}"


def resolve_data_directory() -> Path:
    """`%LOCALAPPDATA%\\LuisIAContable\\SolAgent` -- falla explicitamente
    si `LOCALAPPDATA` no esta definida (nunca cae de vuelta al directorio
    de trabajo actual ni a un directorio temporal compartido)."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ConfigError(
            "La variable de entorno LOCALAPPDATA no esta definida -- no se "
            "puede resolver el directorio de datos del Agente de forma segura."
        )
    return Path(local_app_data) / _APP_DIR_NAME / _AGENT_DIR_NAME


def harden_directory_acl(directory: Path) -> None:
    """Restringe el directorio de datos al usuario de Windows actual --
    mejor esfuerzo real vía `icacls.exe` (ya presente en toda instalacion
    de Windows, sin necesitar `pywin32`), nunca una afirmacion mas fuerte
    de la que Windows realmente garantiza (seccion 16 del encargo). Un
    fallo aqui NO aborta el arranque -- la proteccion primaria del
    material de clave sigue siendo DPAPI (atado al usuario en si, no solo
    a permisos de archivo); esta ACL es defensa en profundidad adicional,
    documentada como mejor esfuerzo, nunca como garantia absoluta."""
    if os.name != "nt":
        return
    try:
        subprocess.run(
            [
                "icacls.exe",
                str(directory),
                "/inheritance:r",
                "/grant:r",
                f"{os.environ.get('USERNAME', '')}:(OI)(CI)F",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # mejor esfuerzo -- ver docstring


def load_config(data_dir: Path) -> AgentConfig:
    config_path = data_dir / _CONFIG_FILENAME
    if not config_path.is_file():
        return AgentConfig()
    try:
        raw = json.loads(config_path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("El archivo de configuracion del Agente existe pero no es JSON valido.") from exc
    if not isinstance(raw, dict):
        raise ConfigError("El archivo de configuracion del Agente no tiene la forma esperada.")
    unknown = set(raw) - _ALLOWED_CONFIG_FIELDS
    if unknown:
        raise ConfigError(
            f"El archivo de configuracion del Agente contiene campos no reconocidos: "
            f"{sorted(unknown)} -- rechazado por seguridad (posible dato fuera de la "
            f"lista blanca de contenido persistente permitido)."
        )
    return replace(AgentConfig(), **raw)


def save_config(data_dir: Path, config: AgentConfig) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    harden_directory_acl(data_dir)
    payload = {
        "broker_host": config.broker_host,
        "pairing_port": config.pairing_port,
        "operational_port": config.operational_port,
        "protocol_version": config.protocol_version,
        "connect_timeout_seconds": config.connect_timeout_seconds,
        "read_timeout_seconds": config.read_timeout_seconds,
    }
    (data_dir / _CONFIG_FILENAME).write_text(json.dumps(payload, indent=2), encoding="ascii")
