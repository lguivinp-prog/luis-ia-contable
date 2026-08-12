"""Gate de un unico worker para la boveda SOL (Fase 5, Bloque 5.1, ver
ADR-0071). `VaultUnlockRegistry` vive exclusivamente en la memoria de un
proceso Python -- si el backend arrancara con mas de un worker (procesos
distintos de uvicorn/gunicorn), cada uno tendria su propio registro
desincronizado del resto, permitiendo que un desbloqueo aparente ser
valido en un worker y no en otro. La v1 de la boveda SOL exige exactamente
un proceso backend.

Mecanismo real inspeccionado: `backend/Dockerfile` invoca
`uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` sin `--workers`,
por lo que uvicorn ya arranca con exactamente 1 proceso trabajador por
defecto -- `BACKEND_WORKERS` ausente se resuelve como 1, consistente con
ese comportamiento real, nunca como "sin limite"."""

from __future__ import annotations


class SolVaultSingleWorkerViolationError(Exception):
    """El backend se configuro con mas de un worker -- la boveda SOL exige
    exactamente uno, en fallo cerrado. El arranque de la aplicacion debe
    abortar antes de aceptar ninguna peticion."""


def enforce_single_worker_gate(backend_workers: int) -> None:
    if backend_workers != 1:
        raise SolVaultSingleWorkerViolationError(
            f"BACKEND_WORKERS={backend_workers}: la boveda SOL exige exactamente 1 worker "
            "de backend (VaultUnlockRegistry es memoria de un unico proceso). "
            "Ajusta BACKEND_WORKERS=1 o retira la variable (se asume 1 por defecto)."
        )
