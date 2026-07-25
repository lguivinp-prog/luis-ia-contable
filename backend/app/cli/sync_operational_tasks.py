"""Sincronizacion administrativa de tareas operativas para UN tenant
explicito (Fase 2, Bloque 1, Seccion 18 del plan). `--tenant-id` es
obligatorio -- nunca elige silenciosamente el primer tenant ni sincroniza
todos por defecto.

El servicio compartido (`task_generation_service.sync_operational_tasks`)
nunca llama `commit()`/`rollback()` -- solo `flush()` (ADR-0037). El CLI
actua como "router": abre su propia sesion, valida el tenant, invoca el
MISMO servicio que usa `POST /tasks/sync` (cero logica duplicada), hace un
unico `commit()` si todo termina bien, `rollback()` ante cualquier
excepcion, y cierra la sesion en `finally`.

Uso: python -m app.cli.sync_operational_tasks --tenant-id <uuid>
"""

import argparse
import sys
import uuid

from app.db.session import SessionLocal
from app.models.tenant import Tenant
from app.services.task_generation_service import sync_operational_tasks


def run(tenant_id: uuid.UUID) -> int:
    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            print(f"[sync-operational-tasks] Tenant {tenant_id} no encontrado", file=sys.stderr)
            return 1

        run_row = sync_operational_tasks(
            db, tenant_id, trigger_source="CLI", triggered_by=None
        )
        db.commit()
        print(
            f"[sync-operational-tasks] Ejecucion {run_row.id} completada para tenant {tenant_id}: "
            f"{run_row.summary}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 -- cualquier fallo revierte todo, sin excepcion
        db.rollback()
        print(f"[sync-operational-tasks] Error, se revirtio la transaccion: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sincronizacion administrativa de tareas operativas para un tenant"
    )
    parser.add_argument("--tenant-id", required=True, help="UUID del tenant a sincronizar")
    args = parser.parse_args()

    try:
        tenant_id = uuid.UUID(args.tenant_id)
    except ValueError:
        print(f"[sync-operational-tasks] --tenant-id invalido: {args.tenant_id}", file=sys.stderr)
        sys.exit(1)

    sys.exit(run(tenant_id))


if __name__ == "__main__":
    main()
