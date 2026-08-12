"""Consultas de la boveda SOL (Fase 5, Bloque 5.1, ver ADR-0068). Ningun
servicio de este bloque ejecuta `db.execute(select(...))` directamente --
toda consulta pasa por este modulo (misma convencion que el resto del
repositorio)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_vault import SolVault, SolVaultDataKey, SolVaultInvitation, SolVaultKeySlot


def get_vault_by_tenant(db: Session, tenant_id: uuid.UUID) -> SolVault | None:
    return db.execute(select(SolVault).where(SolVault.tenant_id == tenant_id)).scalar_one_or_none()


def get_vault_for_update(db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID) -> SolVault | None:
    return db.execute(
        select(SolVault).where(SolVault.id == vault_id, SolVault.tenant_id == tenant_id).with_for_update()
    ).scalar_one_or_none()


def get_active_user_slot(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, user_id: uuid.UUID
) -> SolVaultKeySlot | None:
    return db.execute(
        select(SolVaultKeySlot).where(
            SolVaultKeySlot.tenant_id == tenant_id,
            SolVaultKeySlot.vault_id == vault_id,
            SolVaultKeySlot.user_id == user_id,
            SolVaultKeySlot.kind == "USER",
            SolVaultKeySlot.status == "ACTIVE",
        )
    ).scalar_one_or_none()


def get_pending_recovery_slot(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID
) -> SolVaultKeySlot | None:
    return db.execute(
        select(SolVaultKeySlot).where(
            SolVaultKeySlot.tenant_id == tenant_id,
            SolVaultKeySlot.vault_id == vault_id,
            SolVaultKeySlot.kind == "RECOVERY",
            SolVaultKeySlot.status == "ACTIVE",
            SolVaultKeySlot.recovery_confirmed_at.is_(None),
        )
    ).scalar_one_or_none()


def get_confirmed_recovery_slot(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID
) -> SolVaultKeySlot | None:
    return db.execute(
        select(SolVaultKeySlot).where(
            SolVaultKeySlot.tenant_id == tenant_id,
            SolVaultKeySlot.vault_id == vault_id,
            SolVaultKeySlot.kind == "RECOVERY",
            SolVaultKeySlot.status == "ACTIVE",
            SolVaultKeySlot.recovery_confirmed_at.is_not(None),
        )
    ).scalar_one_or_none()


def get_slot_for_update(
    db: Session, *, tenant_id: uuid.UUID, slot_id: uuid.UUID
) -> SolVaultKeySlot | None:
    return db.execute(
        select(SolVaultKeySlot).where(
            SolVaultKeySlot.id == slot_id, SolVaultKeySlot.tenant_id == tenant_id
        ).with_for_update()
    ).scalar_one_or_none()


def list_active_user_slots(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID
) -> list[SolVaultKeySlot]:
    return list(
        db.execute(
            select(SolVaultKeySlot).where(
                SolVaultKeySlot.tenant_id == tenant_id,
                SolVaultKeySlot.vault_id == vault_id,
                SolVaultKeySlot.kind == "USER",
                SolVaultKeySlot.status == "ACTIVE",
            )
        ).scalars().all()
    )


def list_slots(db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID) -> list[SolVaultKeySlot]:
    return list(
        db.execute(
            select(SolVaultKeySlot)
            .where(SolVaultKeySlot.tenant_id == tenant_id, SolVaultKeySlot.vault_id == vault_id)
            .order_by(SolVaultKeySlot.created_at)
        ).scalars().all()
    )


def get_active_data_key(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, for_update: bool = False
) -> SolVaultDataKey:
    query = select(SolVaultDataKey).where(
        SolVaultDataKey.tenant_id == tenant_id,
        SolVaultDataKey.vault_id == vault_id,
        SolVaultDataKey.status == "ACTIVE",
    )
    if for_update:
        query = query.with_for_update()
    return db.execute(query).scalar_one()


def get_data_key_by_version(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, data_key_version: int
) -> SolVaultDataKey:
    return db.execute(
        select(SolVaultDataKey).where(
            SolVaultDataKey.tenant_id == tenant_id,
            SolVaultDataKey.vault_id == vault_id,
            SolVaultDataKey.data_key_version == data_key_version,
        )
    ).scalar_one()


def list_credentials_for_rotation(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID
) -> list[ClientSolCredential]:
    """Bloqueadas en orden deterministico por `id` -- evita deadlocks entre
    dos rotaciones concurrentes de la misma boveda."""
    return list(
        db.execute(
            select(ClientSolCredential)
            .where(ClientSolCredential.tenant_id == tenant_id, ClientSolCredential.vault_id == vault_id)
            .order_by(ClientSolCredential.id)
            .with_for_update()
        ).scalars().all()
    )


def get_pending_invitation_for_target(
    db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, target_user_id: uuid.UUID
) -> SolVaultInvitation | None:
    return db.execute(
        select(SolVaultInvitation).where(
            SolVaultInvitation.tenant_id == tenant_id,
            SolVaultInvitation.vault_id == vault_id,
            SolVaultInvitation.target_user_id == target_user_id,
            SolVaultInvitation.status == "PENDING",
        ).with_for_update()
    ).scalar_one_or_none()


def get_invitation_for_update(
    db: Session, *, tenant_id: uuid.UUID, invitation_id: uuid.UUID
) -> SolVaultInvitation | None:
    return db.execute(
        select(SolVaultInvitation).where(
            SolVaultInvitation.id == invitation_id, SolVaultInvitation.tenant_id == tenant_id
        ).with_for_update()
    ).scalar_one_or_none()


def get_active_credential_for_client(
    db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> ClientSolCredential | None:
    return db.execute(
        select(ClientSolCredential).where(
            ClientSolCredential.tenant_id == tenant_id,
            ClientSolCredential.client_id == client_id,
            ClientSolCredential.status != "DISABLED",
        )
    ).scalar_one_or_none()


def get_credential_for_update(
    db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID
) -> ClientSolCredential | None:
    return db.execute(
        select(ClientSolCredential).where(
            ClientSolCredential.id == credential_id,
            ClientSolCredential.tenant_id == tenant_id,
            ClientSolCredential.client_id == client_id,
        ).with_for_update()
    ).scalar_one_or_none()
