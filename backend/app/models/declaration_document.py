import enum
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import DATERANGE, JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DeclarationHistoryMixin, TimestampMixin, UUIDPrimaryKeyMixin

# --- enums de dominio (ver "Control documental" del plan / ADR-0033 a ADR-0036) ---


class DocumentItemStatus(str, enum.Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    REQUESTED = "REQUESTED"
    PARTIAL = "PARTIAL"
    RECEIVED = "RECEIVED"
    OBSERVED = "OBSERVED"
    ACCEPTED = "ACCEPTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


_CATALOG_SCOPE_CHECK = (
    "(is_system = true AND tenant_id IS NULL) OR (is_system = false AND tenant_id IS NOT NULL)"
)


class DocumentRequirementCatalog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Catalogo de requisitos documentales, hibrido sistema/personalizado
    (ver ADR-0033). Las filas `is_system=true` (tenant_id NULL) solo se crean
    por semilla o una accion administrativa fuera de los endpoints normales
    de tenant."""

    __tablename__ = "document_requirement_catalog"
    __table_args__ = (
        CheckConstraint(_CATALOG_SCOPE_CHECK, name="ck_document_requirement_catalog_scope"),
        Index(
            "ux_document_requirement_catalog_system_code",
            "code",
            unique=True,
            postgresql_where=sa.text("tenant_id IS NULL"),
        ),
        Index(
            "ux_document_requirement_catalog_tenant_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=sa.text("tenant_id IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ClientDocumentRequirement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Asignacion de un requisito documental a un cliente, con vigencia
    historica (ver ADR-0034). `client_obligation_id` NULL = requisito general
    del cliente, no atado a una obligacion asignada especifica (ver
    ADR-0034/seccion 4 del plan de 3.2 -- reemplaza al antiguo `obligation_id`
    contra el catalogo global)."""

    __tablename__ = "client_document_requirements"
    __table_args__ = (
        UniqueConstraint(
            "id", "tenant_id", "client_id", name="uq_client_document_requirements_id_tenant_client"
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_client_document_requirements_valid_range",
        ),
        # requiere la extension btree_gist; COALESCE trata NULL como valor
        # comparable (mismo patron que tax_schedule_versions/client_tax_categories).
        ExcludeConstraint(
            ("tenant_id", "="),
            ("client_id", "="),
            ("requirement_catalog_id", "="),
            (sa.text("COALESCE(client_obligation_id::text, '')"), "="),
            (sa.text("COALESCE(periodicity, '')"), "="),
            ("validity_range", "&&"),
            using="gist",
            name="ex_client_document_requirements_no_overlap",
        ),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"],
            ["clients.id", "clients.tenant_id"],
            name="fk_client_document_requirements_client_tenant",
        ),
        ForeignKeyConstraint(
            ["client_obligation_id", "tenant_id", "client_id"],
            ["client_obligations.id", "client_obligations.tenant_id", "client_obligations.client_id"],
            name="fk_client_document_requirements_obligation_tenant_client",
        ),
        ForeignKeyConstraint(
            ["responsible_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_client_document_requirements_responsible_tenant",
        ),
        CheckConstraint("reminder_lead_days >= 0", name="ck_client_document_requirements_lead_days"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requirement_catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_requirement_catalog.id"), nullable=False
    )
    client_obligation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    periodicity: Mapped[str] = mapped_column(String(20), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Columna generada por PostgreSQL, usada unicamente por ExcludeConstraint;
    # la app nunca la escribe.
    validity_range: Mapped[object] = mapped_column(
        DATERANGE,
        Computed("daterange(valid_from, valid_until, '[]')", persisted=True),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reminder_lead_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


_ITEM_STATUS_VALUES = tuple(status.value for status in DocumentItemStatus)


class DeclarationDocumentItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Item documental materializado para una declaracion concreta (ver
    "Sincronizacion documental", ADR-0035/0036). `client_id` propio +
    FKs compuestas hacia `declarations` y `client_document_requirements`
    (ambas incluyendo `client_id`) evitan que la base acepte un cruce entre
    clientes del mismo tenant."""

    __tablename__ = "declaration_document_items"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_declaration_document_items_id_tenant"),
        # Auxiliar para la FK compuesta de declaration_document_files (Bloque
        # 5, migracion 0013) -- mismo patron que uq_declarations_id_tenant_client.
        UniqueConstraint(
            "id", "tenant_id", "client_id", name="uq_declaration_document_items_id_tenant_client"
        ),
        # Auxiliar para la FK compuesta de operational_tasks (Fase 2/Bloque 1,
        # migracion 0014) -- forma 3 (item + declaracion), ver ADR-0057.
        UniqueConstraint(
            "id",
            "tenant_id",
            "client_id",
            "declaration_id",
            name="uq_declaration_document_items_id_tenant_client_declaration",
        ),
        UniqueConstraint(
            "tenant_id",
            "declaration_id",
            "client_document_requirement_id",
            name="uq_declaration_document_items_declaration_requirement",
        ),
        CheckConstraint(
            f"status IN {_ITEM_STATUS_VALUES}", name="ck_declaration_document_items_status"
        ),
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id", "client_id"],
            ["declarations.id", "declarations.tenant_id", "declarations.client_id"],
            name="fk_declaration_document_items_declaration_tenant_client",
        ),
        ForeignKeyConstraint(
            ["client_document_requirement_id", "tenant_id", "client_id"],
            [
                "client_document_requirements.id",
                "client_document_requirements.tenant_id",
                "client_document_requirements.client_id",
            ],
            name="fk_declaration_document_items_requirement_tenant_client",
        ),
        ForeignKeyConstraint(
            ["responsible_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_document_items_responsible_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_document_requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DocumentItemStatus.NOT_REQUESTED.value
    )
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reminder_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class DocumentItemStatusHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "document_item_status_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["declaration_document_item_id", "tenant_id"],
            ["declaration_document_items.id", "declaration_document_items.tenant_id"],
            name="fk_document_item_status_history_item_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_document_item_status_history_actor_tenant",
        ),
    )

    declaration_document_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
