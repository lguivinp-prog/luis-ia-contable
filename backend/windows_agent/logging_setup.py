"""Fase 5, Bloque 5.2, Domain E2 -- logging estructurado del Agente
(secciones 47/48 del encargo). Nunca `connection_id`, codigo de pairing,
RUC, usuario/clave SOL, clave privada, material de CSR, token de job, ni
cuerpo de respuesta de credencial -- solo eventos de ciclo de vida con
identificadores no sensibles (id local del agente, job_id, fase, codigo
de error estable, intento/backoff, version de protocolo).

Archivo rotativo acotado (seccion 48): `RotatingFileHandler`, nunca un
archivo sin limite de tamano/retencion."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path

_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB por archivo
_BACKUP_COUNT = 5  # retencion acotada: archivo activo + 5 rotados


def configure_agent_logging(data_dir: Path, *, level: int = logging.INFO) -> logging.Logger:
    """Log rotativo bajo el directorio de datos del agente (nunca un
    directorio temporal compartido, nunca la salida estandar como unico
    destino en produccion -- aunque un handler de consola tambien se
    agrega para uso interactivo, sin secretos en ninguno de los dos)."""
    logger = logging.getLogger("windows_agent")
    logger.setLevel(level)
    logger.handlers.clear()

    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "agent.log", maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # La build empaquetada "windowed" (autoarranque, sin consola) tiene `sys.stderr = None`: solo archivo.
    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    """Mismo patron ya productivo de `app.broker.server._log_event` --
    JSON de una linea, solo campos no sensibles. El LLAMADOR es
    responsable de nunca pasar un campo prohibido aqui; esta funcion no
    intenta adivinar/filtrar por nombre de campo (una lista negra por
    nombre es fragil) -- la disciplina real es que ningun sitio de
    llamada en este paquete construye jamas un `fields` que contenga
    `connection_id`/secretos (verificado por prueba dedicada de barrido
    de logs, no solo por convencion)."""
    logger.info("%s %s", event, json.dumps(fields, default=str, sort_keys=True))
