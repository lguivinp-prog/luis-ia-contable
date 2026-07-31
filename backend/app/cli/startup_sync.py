"""Sincronizacion local explicita de arranque (bloque transversal de
operacion local, ver ADR-0066). Descubre todos los tenants de la
instalacion y ejecuta -- para cada uno, en su PROPIA transaccion
independiente -- la misma sincronizacion de tareas operativas que ya usa
`POST /tasks/sync` y `app.cli.sync_operational_tasks` (cero logica de
dominio duplicada, ver `task_generation_service.sync_operational_tasks`).

NUNCA se invoca desde el ciclo de vida de FastAPI (lifespan/evento startup):
eso podria ejecutarse varias veces por reload o por multiples workers,
mezclaria el ciclo del servidor con logica de dominio, y dificultaria
controlar transacciones/logs/reintentos (ver ADR-0066). Este CLI es el unico
punto de entrada de la sincronizacion de arranque, invocado explicitamente
por `scripts/start-luis-ia-contable.ps1` DESPUES de que las migraciones ya
se aplicaron -- este CLI nunca aplica migraciones ni depende de que backend/
frontend esten saludables.

Exclusion mutua: un advisory lock de sesion de PostgreSQL con una clave de
dominio fija (derivada de un hash estable, nunca cambia entre versiones)
evita que dos accesos directos ejecuten la sincronizacion inicial
simultaneamente -- funciona entre procesos completamente independientes
(incluso en maquinas/contenedores distintos que compartan la misma base),
sin depender de un mutex de Windows ni de un lock file que pueda quedar
huerfano tras un apagado abrupto. Se mantiene sobre una conexion dedicada
durante toda la ejecucion y se libera siempre (`finally`), incluso ante
excepcion.

Integraciones externas (Google Calendar/Drive, SUNAT SOL, Gmail, WhatsApp):
ninguna esta implementada todavia (Bloques 3.2/3.3/3.4, Fases 4/5) -- se
reportan como `SKIPPED_NOT_IMPLEMENTED`, sin ninguna llamada de red, y nunca
afectan el codigo de salida. La ausencia de internet nunca impide la
sincronizacion local.

Resultado clasificado en 4 valores (`StartupSyncResult`), cada uno con un
exit code documentado y estable (`EXIT_CODES`):
  SUCCESS         (0) -- todos los tenants procesados sin fallos (o sin
                         cambios pendientes).
  SKIPPED_LOCKED  (0) -- otra instancia ya sostiene el advisory lock; no es
                         un error, no se sincronizo una segunda vez.
  PARTIAL_FAILURE (2) -- al menos un tenant fallo, pero la ejecucion
                         continuo con los demas -- el sistema sigue siendo
                         utilizable.
  FATAL_FAILURE   (1) -- no fue posible abrir una conexion a PostgreSQL,
                         adquirir el contexto basico (advisory lock) ni
                         descubrir los tenants de forma segura.

Uso: python -m app.cli.startup_sync
"""

from __future__ import annotations

import enum
import json
import sys
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text

from app.core import clock
from app.db.session import SessionLocal
from app.models.tenant import Tenant
from app.services.google_oauth_service import is_google_oauth_configured
from app.services.task_generation_service import sync_operational_tasks

# Clave de dominio fija para el advisory lock -- derivada una sola vez de
# SHA-256("luisiacontable:startup_sync:advisory_lock:v1")[:8 bytes], nunca
# debe recalcularse ni cambiar entre versiones (o dejaria de excluir
# mutuamente instancias de versiones distintas).
ADVISORY_LOCK_KEY = 562294033754473026

# Integraciones externas que este bloque nunca invoca -- ninguna esta
# implementada todavia. Mantener esta lista sincronizada con los bloques
# reales conforme se implementen (3.2 Calendar, 3.3 Drive, 3.4 WhatsApp,
# Fase 4 Gmail, Fase 5 SUNAT SOL) -- nunca anticipar aqui una integracion
# antes de que su bloque exista.
_NOT_IMPLEMENTED_INTEGRATIONS = (
    "google_calendar",
    "google_drive",
    "sunat_sol",
    "gmail",
    "whatsapp",
)


class StartupSyncResult(str, enum.Enum):
    SUCCESS = "SUCCESS"
    SKIPPED_LOCKED = "SKIPPED_LOCKED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FATAL_FAILURE = "FATAL_FAILURE"


# Convencion de exit code estable -- documentada en ADR-0066 y en
# docs/TEST_PLAN.md. `scripts/start-luis-ia-contable.ps1` decide su
# comportamiento (abrir navegador o no) a partir de este codigo, y de la
# linea `STARTUP_SYNC_STATUS=<resultado>` que este CLI siempre imprime.
EXIT_CODES: dict[StartupSyncResult, int] = {
    StartupSyncResult.SUCCESS: 0,
    StartupSyncResult.SKIPPED_LOCKED: 0,
    StartupSyncResult.PARTIAL_FAILURE: 2,
    StartupSyncResult.FATAL_FAILURE: 1,
}


@dataclass
class TenantSyncResult:
    tenant_id: uuid.UUID
    succeeded: bool
    summary: dict | None = None
    error: str | None = None


@dataclass
class StartupSyncReport:
    result: StartupSyncResult
    started_at: str
    completed_at: str
    duration_seconds: float
    tenants: list[TenantSyncResult] = field(default_factory=list)
    integrations_skipped: dict[str, str] = field(default_factory=dict)
    google_oauth_configured: bool = False
    fatal_error: str | None = None

    @property
    def lock_acquired(self) -> bool:
        return self.result != StartupSyncResult.SKIPPED_LOCKED

    @property
    def has_failures(self) -> bool:
        """Conservado por compatibilidad -- equivalente a PARTIAL_FAILURE."""
        return self.result == StartupSyncResult.PARTIAL_FAILURE

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.result]

    def to_json_dict(self) -> dict:
        return {
            "result": self.result.value,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "tenants_total": len(self.tenants),
            "tenants_failed": sum(1 for t in self.tenants if not t.succeeded),
            "tasks_created": sum((t.summary or {}).get("created", 0) for t in self.tenants),
            "tasks_source_updated": sum(
                (t.summary or {}).get("source_updated", 0) for t in self.tenants
            ),
            "tasks_source_completed": sum(
                (t.summary or {}).get("source_completed", 0) for t in self.tenants
            ),
            "tasks_source_cancelled": sum(
                (t.summary or {}).get("source_cancelled", 0) for t in self.tenants
            ),
            "tasks_already_exists": sum(
                (t.summary or {}).get("already_exists", 0) for t in self.tenants
            ),
            "integrations_skipped": self.integrations_skipped,
            "google_oauth_configured": self.google_oauth_configured,
            "fatal_error": self.fatal_error,
            "failed_tenants": [
                {"tenant_id": str(t.tenant_id), "error": t.error}
                for t in self.tenants
                if not t.succeeded
            ],
        }


def _try_acquire_lock(db) -> bool:
    """IMPORTANTE: nunca hace `commit()` aqui. `pg_try_advisory_lock` es un
    lock de SESION (de la conexion fisica subyacente), no de transaccion --
    si esta funcion commiteara, SQLAlchemy podria devolver la conexion al
    pool y usar una distinta para el resto de `run()`, de modo que
    `_release_lock` intentaria liberar el lock desde una conexion que nunca
    lo adquirio (dejandolo huerfano hasta que esa conexion se recicle). La
    misma sesion `db` debe mantenerse abierta, sin commitear, durante todo
    el intervalo entre adquirir y liberar (ver `run()`)."""
    result = db.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
    ).scalar()
    return bool(result)


def _release_lock(db) -> None:
    try:
        db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
        db.commit()
    except Exception:  # noqa: BLE001 -- liberar el lock nunca debe romper el cierre
        pass


def _discover_tenant_ids(db) -> list[uuid.UUID]:
    rows = db.query(Tenant.id).order_by(Tenant.id).all()
    return [row[0] for row in rows]


def _sync_tenant(tenant_id: uuid.UUID) -> TenantSyncResult:
    """Transaccion independiente por tenant -- un fallo en un tenant nunca
    revierte ni bloquea el procesamiento de los demas."""
    db = SessionLocal()
    try:
        run = sync_operational_tasks(db, tenant_id, trigger_source="CLI", triggered_by=None)
        db.commit()
        return TenantSyncResult(tenant_id=tenant_id, succeeded=True, summary=run.summary)
    except Exception as exc:  # noqa: BLE001 -- aislar el fallo de este tenant, continuar con los demas
        db.rollback()
        return TenantSyncResult(tenant_id=tenant_id, succeeded=False, error=str(exc))
    finally:
        db.close()


def _external_integrations_status() -> dict[str, str]:
    return {name: "SKIPPED_NOT_IMPLEMENTED" for name in _NOT_IMPLEMENTED_INTEGRATIONS}


def _short_id(tenant_id: uuid.UUID) -> str:
    return str(tenant_id)[:8]


def _fatal_report(started_at: str, start_wall: float, message: str) -> StartupSyncReport:
    print(f"[startup_sync] FATAL: {message}", file=sys.stderr)
    report = StartupSyncReport(
        result=StartupSyncResult.FATAL_FAILURE,
        started_at=started_at,
        completed_at=clock.utcnow().isoformat(),
        duration_seconds=time.monotonic() - start_wall,
        fatal_error=message,
    )
    print(f"STARTUP_SYNC_STATUS={report.result.value}")
    print(f"STARTUP_SYNC_RESULT_JSON={json.dumps(report.to_json_dict())}")
    return report


def run() -> StartupSyncReport:
    start_wall = time.monotonic()
    started_at = clock.utcnow().isoformat()

    # FATAL_FAILURE, caso 1: ni siquiera se pudo abrir una conexion a
    # PostgreSQL -- no hay ninguna sesion que cerrar en un `finally` todavia.
    try:
        lock_db = SessionLocal()
    except Exception as exc:  # noqa: BLE001
        return _fatal_report(started_at, start_wall, f"no se pudo conectar a PostgreSQL: {exc}")

    try:
        # FATAL_FAILURE, caso 2: la conexion abrio pero no se pudo adquirir
        # ni siquiera el contexto basico (el advisory lock en si).
        try:
            lock_acquired = _try_acquire_lock(lock_db)
        except Exception as exc:  # noqa: BLE001
            return _fatal_report(
                started_at, start_wall, f"no se pudo adquirir el advisory lock: {exc}"
            )

        if not lock_acquired:
            print(
                "[startup_sync] La sincronizacion inicial ya esta en curso en otra instancia -- "
                "se omite esta ejecucion (no es un error)."
            )
            report = StartupSyncReport(
                result=StartupSyncResult.SKIPPED_LOCKED,
                started_at=started_at,
                completed_at=clock.utcnow().isoformat(),
                duration_seconds=time.monotonic() - start_wall,
            )
            print(f"STARTUP_SYNC_STATUS={report.result.value}")
            return report

        # FATAL_FAILURE, caso 3: el lock se adquirio pero ni siquiera se
        # pudo descubrir la lista de tenants de forma segura.
        try:
            tenant_ids = _discover_tenant_ids(lock_db)
        except Exception as exc:  # noqa: BLE001
            return _fatal_report(
                started_at, start_wall, f"no se pudo descubrir la lista de tenants: {exc}"
            )

        print(f"[startup_sync] {len(tenant_ids)} tenant(s) descubierto(s).")

        results: list[TenantSyncResult] = []
        for tenant_id in tenant_ids:
            result = _sync_tenant(tenant_id)
            results.append(result)
            if result.succeeded:
                print(
                    f"[startup_sync] tenant {_short_id(tenant_id)}: OK "
                    f"created={result.summary.get('created')} "
                    f"source_updated={result.summary.get('source_updated')} "
                    f"source_completed={result.summary.get('source_completed')} "
                    f"source_cancelled={result.summary.get('source_cancelled')} "
                    f"already_exists={result.summary.get('already_exists')}"
                )
            else:
                print(
                    f"[startup_sync] tenant {_short_id(tenant_id)}: ERROR ({result.error}) -- "
                    "se continua con los demas tenants",
                    file=sys.stderr,
                )

        integrations = _external_integrations_status()
        google_configured = is_google_oauth_configured()
        for name in integrations:
            print(f"[startup_sync] integracion '{name}': SKIPPED_NOT_IMPLEMENTED")
        google_identity_state = (
            "CONFIGURADA (validacion real diferida, ver ADR-0063)"
            if google_configured
            else "NO_CONFIGURADA"
        )
        print(f"[startup_sync] integracion 'google_identity': {google_identity_state}")

        completed_at = clock.utcnow().isoformat()
        any_failed = any(not r.succeeded for r in results)
        report = StartupSyncReport(
            result=StartupSyncResult.PARTIAL_FAILURE if any_failed else StartupSyncResult.SUCCESS,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=time.monotonic() - start_wall,
            tenants=results,
            integrations_skipped=integrations,
            google_oauth_configured=google_configured,
        )
        print(
            f"[startup_sync] Completado: {len(results)} tenant(s), "
            f"{sum(1 for r in results if not r.succeeded)} fallo(s), "
            f"{report.duration_seconds:.2f}s -- resultado: {report.result.value}"
        )
        print(f"STARTUP_SYNC_STATUS={report.result.value}")
        print(f"STARTUP_SYNC_RESULT_JSON={json.dumps(report.to_json_dict())}")
        return report
    finally:
        _release_lock(lock_db)
        lock_db.close()


def main() -> None:
    report = run()
    sys.exit(report.exit_code)


if __name__ == "__main__":
    main()
