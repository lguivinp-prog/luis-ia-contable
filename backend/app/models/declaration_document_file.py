import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
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


class DocumentFileStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"


_FILE_STATUS_VALUES = tuple(status.value for status in DocumentFileStatus)


class DeclarationDocumentFile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Evidencia fisica real de un item documental (Bloque 5, ver ADR-0053 a
    ADR-0056). Cadena logica de versiones (`logical_file_id`/`version`): una
    carga inicial abre una cadena nueva (`version=1`, `replaces_file_id=NULL`);
    un reemplazo crea una version nueva dentro de la misma cadena
    (`version>1`, `replaces_file_id` apunta a la version anterior). Varias
    cadenas pueden coexistir `ACTIVE` en el mismo item (adjuntos multiples);
    dentro de una misma cadena solo una version puede estar `ACTIVE` a la vez
    (indice unico parcial, no diferible)."""

    __tablename__ = "declaration_document_files"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "tenant_id",
            "client_id",
            "declaration_id",
            "declaration_document_item_id",
            "logical_file_id",
            name="uq_declaration_document_files_chain_scope",
        ),
        UniqueConstraint(
            "tenant_id", "logical_file_id", "version", name="uq_declaration_document_files_chain_version"
        ),
        UniqueConstraint("storage_key", name="uq_declaration_document_files_storage_key"),
        CheckConstraint(f"status IN {_FILE_STATUS_VALUES}", name="ck_declaration_document_files_status"),
        CheckConstraint("size_bytes > 0", name="ck_declaration_document_files_size_positive"),
        CheckConstraint("version >= 1", name="ck_declaration_document_files_version_positive"),
        CheckConstraint(
            "(version = 1 AND replaces_file_id IS NULL) OR "
            "(version > 1 AND replaces_file_id IS NOT NULL)",
            name="ck_declaration_document_files_chain_integrity",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND superseded_at IS NULL AND deleted_at IS NULL "
            "AND deleted_by IS NULL AND deletion_reason IS NULL) OR "
            "(status = 'SUPERSEDED' AND superseded_at IS NOT NULL AND deleted_at IS NULL "
            "AND deleted_by IS NULL AND deletion_reason IS NULL) OR "
            "(status = 'DELETED' AND superseded_at IS NULL AND deleted_at IS NOT NULL "
            "AND deleted_by IS NOT NULL AND deletion_reason IS NOT NULL)",
            name="ck_declaration_document_files_status_coherence",
        ),
        ForeignKeyConstraint(
            ["declaration_document_item_id", "tenant_id", "client_id"],
            [
                "declaration_document_items.id",
                "declaration_document_items.tenant_id",
                "declaration_document_items.client_id",
            ],
            name="fk_declaration_document_files_item_tenant_client",
        ),
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id", "client_id"],
            ["declarations.id", "declarations.tenant_id", "declarations.client_id"],
            name="fk_declaration_document_files_declaration_tenant_client",
        ),
        ForeignKeyConstraint(
            [
                "replaces_file_id",
                "tenant_id",
                "client_id",
                "declaration_id",
                "declaration_document_item_id",
                "logical_file_id",
            ],
            [
                "declaration_document_files.id",
                "declaration_document_files.tenant_id",
                "declaration_document_files.client_id",
                "declaration_document_files.declaration_id",
                "declaration_document_files.declaration_document_item_id",
                "declaration_document_files.logical_file_id",
            ],
            name="fk_declaration_document_files_replaces_chain",
        ),
        ForeignKeyConstraint(
            ["uploaded_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_document_files_uploaded_by_tenant",
        ),
        ForeignKeyConstraint(
            ["deleted_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_document_files_deleted_by_tenant",
        ),
        Index("ix_declaration_document_files_tenant_item", "tenant_id", "declaration_document_item_id"),
        Index("ix_declaration_document_files_tenant_status", "tenant_id", "status"),
        Index("ix_declaration_document_files_tenant_chain", "tenant_id", "logical_file_id"),
        # Indice unico PARCIAL: solo una version ACTIVE por cadena a la vez
        # (no diferible -- ver Seccion 3/11 del plan). Debe reflejarse aqui
        # ademas de en la migracion 0013: las pruebas usan
        # Base.metadata.create_all(), que construye el esquema desde el
        # modelo, no desde las migraciones.
        Index(
            "ux_declaration_document_files_chain_active",
            "tenant_id",
            "logical_file_id",
            unique=True,
            postgresql_where=sa.text("status = 'ACTIVE'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    declaration_document_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    logical_file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Clave logica generada por el servidor -- nunca derivada de
    # original_filename, nunca expuesta en respuestas de la API (ver
    # ADR-0056). El backend de almacenamiento es el unico que traduce esta
    # clave a una ruta fisica real.
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    extension: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DocumentFileStatus.ACTIVE.value
    )
    replaces_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
