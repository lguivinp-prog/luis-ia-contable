"""Comunicaciones asistidas por WhatsApp (Fase 3 / Bloque 3.2, ver
ADR-0067): borradores de mensaje editables, nunca enviados
automaticamente. El envio real ocurre siempre fuera de esta aplicacion
(WhatsApp, tras que el usuario pulsa "Abrir en WhatsApp") -- el sistema
nunca puede verificar que el mensaje realmente se abrio, se entrego o se
leyo, por eso no existe un estado "OPENED_EXTERNALLY" ni ningun estado que
implique confirmacion de WhatsApp. `MARKED_SENT` es exclusivamente una
declaracion humana autodeclarada."""

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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CommunicationChannel(str, enum.Enum):
    WHATSAPP = "WHATSAPP"


class CommunicationDraftStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    OPEN_REQUESTED = "OPEN_REQUESTED"
    MARKED_SENT = "MARKED_SENT"
    DISCARDED = "DISCARDED"


_STATUS_VALUES = tuple(status.value for status in CommunicationDraftStatus)

# Formato E.164 basico: "+" seguido de un digito 1-9 y entre 7 y 14 digitos
# mas (8 a 15 digitos en total tras el "+") -- misma regla que la
# normalizacion de telefono (Seccion 7 del pedido de Bloque 3.2).
_PHONE_E164_CHECK = r"recipient_phone_e164 ~ '^\+[1-9][0-9]{7,14}$'"

# Coherencia exacta por estado (ver Seccion 6 del pedido): DISCARDED es el
# unico estado donde open_requested_at puede ser NULL o NOT NULL, porque se
# puede descartar un borrador antes o despues de haber solicitado abrir
# WhatsApp.
_STATUS_COHERENCE_CHECK = """
(
  status = 'DRAFT' AND open_requested_at IS NULL AND marked_sent_at IS NULL AND marked_sent_by IS NULL
  AND discarded_at IS NULL AND discarded_by IS NULL
)
OR
(
  status = 'OPEN_REQUESTED' AND open_requested_at IS NOT NULL
  AND marked_sent_at IS NULL AND marked_sent_by IS NULL
  AND discarded_at IS NULL AND discarded_by IS NULL
)
OR
(
  status = 'MARKED_SENT' AND open_requested_at IS NOT NULL
  AND marked_sent_at IS NOT NULL AND marked_sent_by IS NOT NULL
  AND discarded_at IS NULL AND discarded_by IS NULL
)
OR
(
  status = 'DISCARDED' AND discarded_at IS NOT NULL AND discarded_by IS NOT NULL
  AND marked_sent_at IS NULL AND marked_sent_by IS NULL
)
""".strip()


class CommunicationDraft(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Borrador de comunicacion asistida (WhatsApp via `wa.me`). El texto y
    el numero de telefono quedan congelados como snapshot en el momento de
    crear el borrador -- nunca se recalculan si el cliente cambia su
    `whatsapp_number` despues (ver Seccion 7 del pedido de Bloque 3.2)."""

    __tablename__ = "communication_drafts"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_communication_drafts_id_tenant"),
        CheckConstraint("channel IN ('WHATSAPP')", name="ck_communication_drafts_channel"),
        CheckConstraint(f"status IN {_STATUS_VALUES}", name="ck_communication_drafts_status"),
        CheckConstraint("version >= 1", name="ck_communication_drafts_version_positive"),
        CheckConstraint(
            "length(trim(body)) BETWEEN 1 AND 4000", name="ck_communication_drafts_body_length"
        ),
        CheckConstraint(_PHONE_E164_CHECK, name="ck_communication_drafts_phone_e164_format"),
        CheckConstraint(_STATUS_COHERENCE_CHECK, name="ck_communication_drafts_status_coherence"),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_communication_drafts_client_tenant",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_communication_drafts_created_by_tenant",
        ),
        ForeignKeyConstraint(
            ["marked_sent_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_communication_drafts_marked_sent_by_tenant",
        ),
        ForeignKeyConstraint(
            ["discarded_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_communication_drafts_discarded_by_tenant",
        ),
        ForeignKeyConstraint(
            ["related_task_id", "tenant_id"], ["operational_tasks.id", "operational_tasks.tenant_id"],
            name="fk_communication_drafts_related_task_tenant",
        ),
        ForeignKeyConstraint(
            ["related_document_file_id", "tenant_id", "client_id"],
            ["declaration_document_files.id", "declaration_document_files.tenant_id",
             "declaration_document_files.client_id"],
            name="fk_communication_drafts_related_document_tenant_client",
        ),
        Index("ix_communication_drafts_tenant_client", "tenant_id", "client_id"),
        Index("ix_communication_drafts_tenant_status", "tenant_id", "status"),
        Index("ix_communication_drafts_tenant_created_by", "tenant_id", "created_by"),
        Index(
            "ix_communication_drafts_related_task",
            "related_task_id",
            postgresql_where="related_task_id IS NOT NULL",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    related_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    related_document_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CommunicationChannel.WHATSAPP.value
    )
    template_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_phone_display: Mapped[str] = mapped_column(String(30), nullable=False)
    recipient_phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CommunicationDraftStatus.DRAFT.value
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    open_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    marked_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    marked_sent_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discarded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
