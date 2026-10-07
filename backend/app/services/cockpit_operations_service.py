"""Estados operativos MANUALES por (tenant, cliente, periodo) (M1). Lectura sin efectos; escritura con creacion PEREZOSA,
concurrencia optimista por `row_version` y un historial compacto (quien, cuando, campo, de -> a)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.client_period_operation import (
    DEFAULT_ACCOUNTING,
    DEFAULT_BUZON,
    DEFAULT_COMMUNICATION,
    ClientPeriodOperation,
    ClientPeriodOperationHistory,
)
from app.models.tax_period import TaxPeriod
from app.models.user import User

STATUS_FIELDS = ("buzon_status", "accounting_status", "client_communication_status")
_DEFAULTS = {
    "buzon_status": DEFAULT_BUZON,
    "accounting_status": DEFAULT_ACCOUNTING,
    "client_communication_status": DEFAULT_COMMUNICATION,
    "notes": None,
}


class OperationsScopeNotFoundError(Exception):
    """El cliente o el periodo no existen en el tenant."""


class OperationsVersionConflictError(Exception):
    def __init__(self, current_row_version: int) -> None:
        super().__init__("row_version desactualizado")
        self.current_row_version = current_row_version


@dataclass
class OperationsState:
    buzon_status: str
    accounting_status: str
    client_communication_status: str
    notes: str | None
    row_version: int  # 0 = aun no existe fila (valores por defecto, nada persistido)
    updated_at: datetime | None
    updated_by: uuid.UUID | None


def _check_scope(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID) -> None:
    client = db.execute(select(Client.id).where(Client.id == client_id, Client.tenant_id == tenant_id)).first()
    period = db.execute(select(TaxPeriod.id).where(TaxPeriod.id == period_id, TaxPeriod.tenant_id == tenant_id)).first()
    if client is None or period is None:
        raise OperationsScopeNotFoundError()


def _state(row: ClientPeriodOperation | None) -> OperationsState:
    if row is None:
        return OperationsState(**_DEFAULTS, row_version=0, updated_at=None, updated_by=None)
    return OperationsState(
        buzon_status=row.buzon_status,
        accounting_status=row.accounting_status,
        client_communication_status=row.client_communication_status,
        notes=row.notes,
        row_version=row.row_version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def _select_scope(tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID):
    return select(ClientPeriodOperation).where(
        ClientPeriodOperation.tenant_id == tenant_id,
        ClientPeriodOperation.client_id == client_id,
        ClientPeriodOperation.tax_period_id == period_id,
    )


def get_operations(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID) -> OperationsState:
    """Solo lectura: si no hay fila devuelve los valores por defecto y NO crea nada."""
    _check_scope(db, tenant_id, client_id, period_id)
    return _state(db.execute(_select_scope(tenant_id, client_id, period_id)).scalar_one_or_none())


def _normalize_notes(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def update_operations(
    db: Session,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    client_id: uuid.UUID,
    period_id: uuid.UUID,
    *,
    changes: dict[str, str | None],
    expected_row_version: int,
) -> OperationsState:
    """`changes`: SOLO los campos presentes en la solicitud. Sin cambios efectivos no se crea ni se versiona nada.
    `expected_row_version=0` = "aun no existe fila" (creacion perezosa). El llamador hace commit/rollback."""
    _check_scope(db, tenant_id, client_id, period_id)
    wanted = {
        field: (_normalize_notes(value) if field == "notes" else value) for field, value in changes.items()
    }

    row = db.execute(_select_scope(tenant_id, client_id, period_id).with_for_update()).scalar_one_or_none()
    if row is None:
        if expected_row_version != 0:
            raise OperationsVersionConflictError(0)
        effective = {f: v for f, v in wanted.items() if v != _DEFAULTS[f]}
        if not effective:
            return _state(None)
        inserted = db.execute(
            pg_insert(ClientPeriodOperation)
            .values(tenant_id=tenant_id, client_id=client_id, tax_period_id=period_id, row_version=1,
                    updated_by=actor_user_id, **effective)
            .on_conflict_do_nothing(index_elements=["tenant_id", "client_id", "tax_period_id"])
            .returning(ClientPeriodOperation.id)
        ).first()
        if inserted is None:  # otra transaccion creo la fila entre la lectura y el insert
            current = db.execute(_select_scope(tenant_id, client_id, period_id)).scalar_one()
            raise OperationsVersionConflictError(current.row_version)
        row = db.execute(_select_scope(tenant_id, client_id, period_id)).scalar_one()
        previous = dict(_DEFAULTS)
        diffs = {f: (previous[f], v) for f, v in effective.items()}
        for field, (old, new) in diffs.items():
            _record(db, row, field, old, new, actor_user_id)
        return _state(row)

    if row.row_version != expected_row_version:
        raise OperationsVersionConflictError(row.row_version)
    diffs = {f: (getattr(row, f), v) for f, v in wanted.items() if getattr(row, f) != v}
    if not diffs:
        return _state(row)
    for field, (_, new) in diffs.items():
        setattr(row, field, new)
    row.row_version += 1
    row.updated_by = actor_user_id
    db.flush()
    for field, (old, new) in diffs.items():
        _record(db, row, field, old, new, actor_user_id)
    db.refresh(row)
    return _state(row)


def _record(
    db: Session, row: ClientPeriodOperation, field: str, old: str | None, new: str | None, actor_user_id: uuid.UUID
) -> None:
    db.add(
        ClientPeriodOperationHistory(
            tenant_id=row.tenant_id,
            operation_id=row.id,
            client_id=row.client_id,
            tax_period_id=row.tax_period_id,
            field_name=field,
            from_value=old,
            to_value=new,
            row_version_after=row.row_version,
            actor_user_id=actor_user_id,
        )
    )
    db.flush()


def list_history(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID, limit: int = 100
) -> list[tuple[ClientPeriodOperationHistory, str | None]]:
    _check_scope(db, tenant_id, client_id, period_id)
    rows = db.execute(
        select(ClientPeriodOperationHistory, User.full_name)
        .outerjoin(User, (User.id == ClientPeriodOperationHistory.actor_user_id) & (User.tenant_id == tenant_id))
        .where(
            ClientPeriodOperationHistory.tenant_id == tenant_id,
            ClientPeriodOperationHistory.client_id == client_id,
            ClientPeriodOperationHistory.tax_period_id == period_id,
        )
        .order_by(ClientPeriodOperationHistory.created_at.desc(), ClientPeriodOperationHistory.row_version_after.desc())
        .limit(limit)
    ).all()
    return [(entry, actor_name) for entry, actor_name in rows]
