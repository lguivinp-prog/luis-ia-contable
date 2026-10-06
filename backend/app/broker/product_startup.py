"""Fase 5, Bloque 5.2, Domain E2 -- composicion de arranque PRODUCTIVO del
broker SOL (cierra `BROKER_PRODUCT_STARTUP_ENTRYPOINT_EXISTS=NO`, hallazgo
de Domain E1-B.1). Punto de entrada ESTRECHO invocado por `app/main.py`
(mismo patron ya productivo de `@app.on_event("startup")` para
`_warm_up_caches` -- nunca `startup_sync`, que ADR-0066 prohibe
explicitamente en un hook de FastAPI por ser una operacion de dominio
multi-tenant que podria re-ejecutarse; el arranque del broker es gestion
de recursos de infraestructura ligada al ciclo de vida del proceso --
misma categoria que abrir el engine de BD, no una operacion de dominio).

Deliberadamente NO un CLI/proceso separado: `VaultUnlockRegistry` y
`SecretDeliveryGrantRegistry` son memoria de UN SOLO proceso Python (ver
`sol_vault_worker_gate.py`/ADR-0071) -- el broker debe compartir el MISMO
proceso que el resto del backend por referencia directa de objeto, nunca
un proceso aparte con su propio registro desincronizado."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.broker import pki
from app.broker.server import BrokerServer
from app.core.config import Settings
from app.core.sol_vault_worker_gate import enforce_single_worker_gate


class BrokerStartupError(Exception):
    """Fallo cerrado y accionable al intentar arrancar el broker productivo
    -- NUNCA se autogenera una CA como respaldo silencioso (seccion 27 del
    encargo: `PRODUCTION_CA_AUTOGENERATION=NO`). El mensaje nunca incluye
    material de clave/certificado, solo la ruta configurada y la causa."""


def start_production_broker(settings: Settings, session_factory: sessionmaker) -> BrokerServer:
    """Punto de entrada REAL de arranque -- no un constructor de solo
    pruebas (seccion 72 del encargo: "No test-only constructor as sole
    proof"). Pasos, en orden:

    1. Reafirma el gate de un unico worker (ya aplicado por `main.py` al
       importar, pero se reafirma aqui porque esta funcion es tambien el
       punto de entrada de la prueba de integracion de este modulo, que no
       pasa por `main.py`).
    2. Carga la CA YA EXISTENTE en `settings.sol_broker_ca_dir` -- nunca
       `pki.load_or_generate_ca`. Un directorio vacio o material invalido
       aborta con `BrokerStartupError`, nunca arranca con una CA generada
       sobre la marcha.
    3. Construye `BrokerServer` con CADA timeout/puerto/host explicitamente
       tomado de `settings` -- nunca los defaults del constructor de
       `BrokerServer` (seccion 25 del encargo:
       `BROKER_TIMEOUT_EXPLICITLY_WIRED_FROM_SETTINGS=YES`). `host` sigue
       siendo el unico validado por `BrokerServer.__init__` contra
       `_ALLOWED_HOSTS` (loopback) -- esta funcion no relaja esa
       validacion, solo la reafirma pasando siempre el valor configurado
       (que en este repositorio nunca es otra cosa que `127.0.0.1`).
    4. Arranca los dos listeners (pairing + operacional) y devuelve la
       instancia -- el llamador (`main.py`) es responsable de guardarla y
       llamar a `stop_production_broker` en el apagado."""
    enforce_single_worker_gate(settings.backend_workers)

    ca_dir = Path(settings.sol_broker_ca_dir)
    try:
        ca = pki.load_ca(ca_dir=ca_dir)
    except pki.CaMaterialMissingError as exc:
        raise BrokerStartupError(
            f"BROKER_CA_MATERIAL_MISSING: no se encontro material de CA en "
            f"'{ca_dir}'. Provisiona la CA del broker antes de activar "
            f"SOL_BROKER_ENABLED=true -- este arranque nunca autogenera una "
            f"CA de respaldo en produccion."
        ) from exc
    except pki.CaMaterialInvalidError as exc:
        raise BrokerStartupError(
            f"BROKER_CA_MATERIAL_INVALID: el material de CA en '{ca_dir}' "
            f"existe pero no es valido. Revisa/reemplaza el material -- "
            f"este arranque nunca autogenera una CA de respaldo en "
            f"produccion."
        ) from exc

    broker = BrokerServer(
        host=settings.sol_broker_host,
        pairing_port=settings.sol_broker_pairing_port,
        operational_port=settings.sol_broker_operational_port,
        ca=ca,
        session_factory=session_factory,
        max_concurrent_connections=settings.sol_broker_max_concurrent_connections,
        handshake_timeout_seconds=settings.sol_broker_tls_handshake_timeout_seconds,
        idle_timeout_seconds=settings.sol_broker_idle_timeout_seconds,
        read_timeout_seconds=settings.sol_broker_read_timeout_seconds,
    )
    broker.start()
    return broker


def stop_production_broker(broker: BrokerServer) -> None:
    broker.stop()
