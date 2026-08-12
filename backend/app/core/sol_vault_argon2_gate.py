"""Gate de concurrencia Argon2id (Fase 5, Bloque 5.1, ver ADR-0069): las
derivaciones Argon2id productivas son deliberadamente costosas en CPU y
memoria (~700ms, ~384 MiB con los parametros productivos, ver
`sol_vault_crypto.production_argon2_params`) -- permitir un numero
ilimitado de derivaciones concurrentes en el mismo proceso backend podria
agotar la memoria disponible bajo carga real (varios desbloqueos/altas de
credencial simultaneos). Validado experimentalmente en el Spike C
(`spikes/sol_vault/argon2_gate.py`); esta es la version productiva, sin
depender del spike."""

from __future__ import annotations

import threading

from app.core import sol_vault_crypto as crypto

_DEFAULT_TIMEOUT_SECONDS = 5.0


class Argon2GateTimeoutError(Exception):
    """La espera acotada para adquirir el gate de concurrencia Argon2id
    expiro sin conseguir un permiso -- fallo cerrado, nunca se ejecuta la
    derivacion sin haber adquirido el gate primero."""


class Argon2ConcurrencyGate:
    """Semaforo de conteo configurable (por defecto 1, ver ADR-0069): como
    mucho `max_concurrent` derivaciones Argon2id en curso simultaneamente a
    traves de esta misma instancia."""

    def __init__(self, max_concurrent: int = 1) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent debe ser >= 1")
        self.max_concurrent = max_concurrent
        self._semaphore = threading.Semaphore(max_concurrent)

    def derive_kek(
        self,
        password: str,
        salt: bytes,
        params: crypto.Argon2Params,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> bytes:
        acquired = self._semaphore.acquire(timeout=timeout_seconds)
        if not acquired:
            raise Argon2GateTimeoutError(
                f"no se pudo adquirir el gate de concurrencia Argon2id en {timeout_seconds}s "
                f"(max_concurrent={self.max_concurrent})"
            )
        try:
            return crypto.derive_kek(password, salt, params)
        finally:
            self._semaphore.release()


# Instancia unica de proceso -- todo el servicio de la boveda SOL debe
# derivar KEKs exclusivamente a traves de este gate compartido, nunca
# invocando `sol_vault_crypto.derive_kek` directamente fuera de pruebas.
argon2_gate = Argon2ConcurrencyGate(max_concurrent=1)
