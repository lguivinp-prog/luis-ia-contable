"""Estados operativos MANUALES por (tenant, cliente, periodo) del cockpit (M1). Solo lo que no se puede derivar:
Buzon, Contabilidad (estado operativo del trabajo hecho en CONTASIS/DARWIN; sin asientos ni libros) y Comunicacion al
cliente. Documentos, SIRE, declaraciones, impuesto, honorarios, vencimientos, riesgo y fase NO se persisten."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BuzonStatus(str, enum.Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    REVIEWED_CLEAR = "REVIEWED_CLEAR"
    REVIEWED_ACTION_REQUIRED = "REVIEWED_ACTION_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AccountingStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEWED = "REVIEWED"
    COMPLETED = "COMPLETED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ClientCommunicationStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    PREPARED = "PREPARED"
    SENT = "SENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


DEFAULT_BUZON = BuzonStatus.NOT_REVIEWED.value
DEFAULT_ACCOUNTING = AccountingStatus.NOT_STARTED.value
DEFAULT_COMMUNICATION = ClientCommunicationStatus.NOT_STARTED.value
HISTORY_FIELDS = ("buzon_status", "accounting_status", "client_communication_status", "notes")


def _in(enum_cls) -> str:
    return "(" + ", ".join(f"'{m.value}'" for m in enum_cls) + ")"


class ClientPeriodOperation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_period_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "client_id", "tax_period_id", name="uq_client_period_operations_scope"),
        UniqueConstraint("id", "tenant_id", name="uq_client_period_operations_id_tenant"),
        UniqueConstraint("id", "tenant_id", "client_id", "tax_period_id", name="uq_client_period_operations_id_scope"),
        CheckConstraint(f"buzon_status IN {_in(BuzonStatus)}", name="ck_client_period_operations_buzon_status"),
        CheckConstraint(
            f"accounting_status IN {_in(AccountingStatus)}", name="ck_client_period_operations_accounting_status"
        ),
        CheckConstraint(
            f"client_communication_status IN {_in(ClientCommunicationStatus)}",
            name="ck_client_period_operations_communication_status",
        ),
        CheckConstraint("row_version >= 1", name="ck_client_period_operations_row_version_positive"),
        CheckConstraint("notes IS NULL OR length(notes) <= 2000", name="ck_client_period_operations_notes_length"),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_client_period_operations_client_tenant",
        ),
        ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"], ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_client_period_operations_period_tenant",
        ),
        ForeignKeyConstraint(
            ["updated_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_period_operations_updated_by_tenant",
        ),
        Index("ix_client_period_operations_tenant_period", "tenant_id", "tax_period_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    buzon_status: Mapped[str] = mapped_column(String(30), nullable=False, default=DEFAULT_BUZON)
    accounting_status: Mapped[str] = mapped_column(String(20), nullable=False, default=DEFAULT_ACCOUNTING)
    client_communication_status: Mapped[str] = mapped_column(String(20), nullable=False, default=DEFAULT_COMMUNICATION)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ClientPeriodOperationHistory(Base, UUIDPrimaryKeyMixin):
    """Historial compacto append-only: quien, cuando, cliente, periodo, campo, de -> a."""

    __tablename__ = "client_period_operation_history"
    __table_args__ = (
        CheckConstraint(
            "field_name IN " + "(" + ", ".join(f"'{f}'" for f in HISTORY_FIELDS) + ")",
            name="ck_client_period_operation_history_field",
        ),
        CheckConstraint("row_version_after >= 1", name="ck_client_period_operation_history_version_positive"),
        ForeignKeyConstraint(
            ["operation_id", "tenant_id", "client_id", "tax_period_id"],
            [
                "client_period_operations.id",
                "client_period_operations.tenant_id",
                "client_period_operations.client_id",
                "client_period_operations.tax_period_id",
            ],
            name="fk_client_period_operation_history_scope",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_period_operation_history_actor_tenant",
        ),
        Index(
            "ix_client_period_operation_history_scope_created", "tenant_id", "client_id", "tax_period_id", "created_at"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(40), nullable=False)
    from_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
