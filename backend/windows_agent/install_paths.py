"""Fase 5, Bloque 5.2, Domain G -- rutas de la instalacion EMPAQUETADA del Agente Windows.

Separacion obligatoria (seccion 7 del encargo):

    %LOCALAPPDATA%\\LuisIAContable\\SolAgentApp\\   binarios / runtime / navegador (se reemplazan al actualizar)
        app\\        ejecutables + `_internal` (PyInstaller one-folder) + build_info.json + MANIFEST.sha256
        browsers\\   navegador propio de la app (nunca un perfil de Chrome/Edge)
    %LOCALAPPDATA%\\LuisIAContable\\SolAgent\\       identidad / configuracion / logs (NUNCA se reemplazan al actualizar)

El navegador instalado es solo el MOTOR: el estado autenticado sigue siendo efimero (BrowserContext sin perfil
persistente, sin storageState, sin trazas ni video). Este modulo no maneja secretos."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BUILD_INFO_FILENAME = "build_info.json"
BROKER_CA_DIRNAME = "broker_ca"  # dentro del directorio de DATOS del agente (solo certificado PUBLICO)
CA_CERT_FILENAME = "ca.cert.pem"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_directory() -> Path | None:
    """Directorio de la aplicacion empaquetada (donde esta el .exe); `None` fuera de una build congelada."""
    return Path(sys.executable).resolve().parent if is_frozen() else None


def installed_browsers_directory() -> Path | None:
    """`SolAgentApp\\browsers` -- hermano de `SolAgentApp\\app`. `None` si no es una instalacion congelada."""
    app_dir = app_directory()
    return app_dir.parent / "browsers" if app_dir is not None else None


def read_build_info() -> dict[str, object] | None:
    app_dir = app_directory()
    if app_dir is None:
        return None
    path = app_dir / BUILD_INFO_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def default_ca_directory(data_dir: Path) -> Path:
    """Copia local del certificado PUBLICO de la CA del broker (ca.cert.pem). Jamas contiene la clave privada."""
    return data_dir / BROKER_CA_DIRNAME
