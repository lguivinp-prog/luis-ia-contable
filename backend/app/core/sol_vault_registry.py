"""`VaultUnlockRegistry` productivo (Fase 5, Bloque 5.1, ver ADR-0071):
registro puramente en memoria de proceso, NUNCA persistido en PostgreSQL,
Redis ni archivo alguno. Indexado por `(tenant_id, vault_id, user_id,
app_session_id) -> handle`, de forma que dos sesiones distintas del mismo
usuario coexisten sin sobrescribirse. `app_session_id` es siempre el `id`
real de una fila de `sessions` (`app.models.session.Session`) -- nunca un
identificador inventado.

Invariante de v1 (ver `sol_vault_worker_gate.py`): un unico proceso
backend -- el registro es memoria de ese unico proceso, por lo que un
reinicio del backend bloquea automaticamente todas las bovedas (no existe
ningun mecanismo de persistencia que sobreviva el reinicio, ni deberia
existir uno)."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.sol_vault_crypto import zeroize
from app.models.session import Session as AppSession

ALLOWED_OPERATIONS = frozenset(
    {
        "CREDENTIAL_MANAGEMENT",
        "SLOT_MANAGEMENT",
        "DATA_KEY_ROTATION",
        "RECOVERY",
        "VERIFY_ACCESS",
        # Fase 5, Bloque 5.2, Domain E1-A.1/E1-B (ver
        # docs/phase5/block5_2/BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md
        # seccion 4): capacidad EXCLUSIVA para autorizar/re-verificar el
        # desbloqueo al autorizar o entregar una credencial SOL a un
        # agente Windows emparejado -- NUNCA se reutiliza `VERIFY_ACCESS`
        # (semantica mas debil, sin autorizar liberacion de secreto
        # alguna). No es un permiso RBAC, no es un enum de BD, no
        # requiere migracion -- solo vocabulario interno de este
        # `frozenset` de Python.
        "CREDENTIAL_DELIVERY",
    }
)


class VaultLockedError(Exception):
    """No existe ninguna autorizacion de desbloqueo vigente para esta
    combinacion (tenant, vault, user, session) -- la boveda esta LOCKED
    (estado derivado, ver ADR-0068)."""


class VaultUnlockExpiredError(Exception):
    """La autorizacion vencio, fue revocada, o la sesion de aplicacion
    subyacente ya no es valida (revocada/expirada/no coincide)."""


class VaultOperationNotAuthorizedError(Exception):
    """La operacion solicitada no esta en `allowed_operations`, o el
    `client_id` no esta dentro del alcance autorizado."""


@dataclass
class UnlockEntry:
    vault_root_key: bytearray
    tenant_id: uuid.UUID
    vault_id: uuid.UUID
    user_id: uuid.UUID
    app_session_id: uuid.UUID
    allowed_operations: frozenset[str]
    authorized_client_ids: frozenset[uuid.UUID] | None
    issued_at: datetime
    expires_at: datetime
    revoked: bool = False


class VaultUnlockRegistry:
    """Un unico registro por proceso backend. Nunca persiste
    `vault_root_key` en ningun medio distinto de esta estructura en
    memoria; nunca expone un handle a traves de ninguna respuesta HTTP."""

    def __init__(self) -> None:
        self._entries: dict[str, UnlockEntry] = {}
        self._index: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID], str] = {}
        self._lock_guard = threading.Lock()

    def issue(
        self,
        *,
        vault_root_key: bytes,
        tenant_id: uuid.UUID,
        vault_id: uuid.UUID,
        user_id: uuid.UUID,
        app_session_id: uuid.UUID,
        allowed_operations: frozenset[str],
        authorized_client_ids: frozenset[uuid.UUID] | None,
        ttl_seconds: int,
        now: datetime,
    ) -> str:
        if not allowed_operations.issubset(ALLOWED_OPERATIONS):
            raise ValueError(f"allowed_operations contiene valores no reconocidos: {allowed_operations}")
        handle = os.urandom(32).hex()
        entry = UnlockEntry(
            vault_root_key=bytearray(vault_root_key),
            tenant_id=tenant_id,
            vault_id=vault_id,
            user_id=user_id,
            app_session_id=app_session_id,
            allowed_operations=allowed_operations,
            authorized_client_ids=authorized_client_ids,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        key = (tenant_id, vault_id, user_id, app_session_id)
        with self._lock_guard:
            previous_handle = self._index.get(key)
            if previous_handle is not None:
                self._purge_handle_locked(previous_handle)
            self._entries[handle] = entry
            self._index[key] = handle
        return handle

    def _purge_handle_locked(self, handle: str) -> None:
        """Debe llamarse ya bajo self._lock_guard."""
        entry = self._entries.pop(handle, None)
        if entry is not None:
            zeroize(entry.vault_root_key)
        for k, h in list(self._index.items()):
            if h == handle:
                del self._index[k]

    def lock(
        self, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, user_id: uuid.UUID, app_session_id: uuid.UUID
    ) -> None:
        """Bloqueo manual: purga inmediata, sin esperar el TTL."""
        key = (tenant_id, vault_id, user_id, app_session_id)
        with self._lock_guard:
            handle = self._index.get(key)
            if handle is not None:
                self._purge_handle_locked(handle)

    def lock_all_for_session(self, app_session_id: uuid.UUID) -> None:
        """Invalida todas las entradas atadas a una sesion de aplicacion
        concreta -- usado cuando esa sesion se revoca/cierra (logout,
        revocacion manual, expiracion de inactividad)."""
        with self._lock_guard:
            handles = {h for k, h in self._index.items() if k[3] == app_session_id}
            for h in handles:
                self._purge_handle_locked(h)

    def reset(self) -> None:
        """Simula/representa un reinicio del backend: purga TODO el
        registro, de todos los tenants. Se invoca automaticamente al
        arrancar el proceso (el registro nace vacio), y esta disponible
        para pruebas."""
        with self._lock_guard:
            for handle in list(self._entries.keys()):
                self._purge_handle_locked(handle)

    def entry_count(self) -> int:
        with self._lock_guard:
            return len(self._entries)

    def get_entry(self, handle: str) -> UnlockEntry | None:
        with self._lock_guard:
            return self._entries.get(handle)

    def get_handle(
        self, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, user_id: uuid.UUID, app_session_id: uuid.UUID
    ) -> str | None:
        with self._lock_guard:
            return self._index.get((tenant_id, vault_id, user_id, app_session_id))

    def resolve(
        self,
        db: DbSession,
        *,
        tenant_id: uuid.UUID,
        vault_id: uuid.UUID,
        user_id: uuid.UUID,
        app_session_id: uuid.UUID,
        operation: str,
        now: datetime,
        client_id: uuid.UUID | None = None,
    ) -> UnlockEntry:
        """Resuelve y verifica en vivo una autorizacion vigente: `sessions.id`
        coincide, `tenant_id`/`user_id` coinciden, `revoked_at IS NULL`,
        sesion no vencida, autorizacion no vencida, operacion incluida en
        `allowed_operations`, `client_id` incluido cuando existe alcance
        limitado. Cualquier fallo de sesion/expiracion purga la entrada."""
        key = (tenant_id, vault_id, user_id, app_session_id)
        with self._lock_guard:
            handle = self._index.get(key)
            entry = self._entries.get(handle) if handle else None

        if entry is None:
            raise VaultLockedError("no existe una autorizacion de desbloqueo vigente")

        if entry.revoked or entry.expires_at <= now:
            with self._lock_guard:
                self._purge_handle_locked(handle)
            raise VaultUnlockExpiredError("la autorizacion de desbloqueo vencio o fue revocada")

        session_row = db.execute(
            select(AppSession).where(AppSession.id == app_session_id)
        ).scalar_one_or_none()
        if session_row is None or session_row.revoked_at is not None or session_row.expires_at <= now:
            with self._lock_guard:
                self._purge_handle_locked(handle)
            raise VaultUnlockExpiredError("la sesion de aplicacion subyacente ya no es valida")

        if session_row.tenant_id != tenant_id or session_row.user_id != user_id:
            with self._lock_guard:
                self._purge_handle_locked(handle)
            raise VaultUnlockExpiredError("la sesion no corresponde al tenant/usuario esperado")

        if operation not in entry.allowed_operations:
            raise VaultOperationNotAuthorizedError(
                f"la operacion '{operation}' no esta autorizada por esta autorizacion de desbloqueo"
            )

        if entry.authorized_client_ids is not None and client_id is not None:
            if client_id not in entry.authorized_client_ids:
                raise VaultOperationNotAuthorizedError(
                    f"el cliente {client_id} no esta dentro del alcance autorizado"
                )

        return entry


# Instancia unica de proceso -- todo el servicio de la boveda SOL debe
# operar exclusivamente a traves de este registro compartido, nunca
# instanciando uno nuevo fuera de pruebas.
vault_unlock_registry = VaultUnlockRegistry()
