"""Almacenamiento real de archivos documentales (Bloque 5): tabla
declaration_document_files con cadena logica de versiones (logical_file_id/
version), indice unico parcial para una sola version ACTIVE por cadena, FK
compuesta amplia para el auto-enlace de reemplazo (ver ADR-0053 a ADR-0056).

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-25

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FILE_STATUS_CHECK = "status IN ('ACTIVE', 'SUPERSEDED', 'DELETED')"

_CHAIN_INTEGRITY_CHECK = (
    "(version = 1 AND replaces_file_id IS NULL) OR "
    "(version > 1 AND replaces_file_id IS NOT NULL)"
)

_STATUS_COHERENCE_CHECK = (
    "(status = 'ACTIVE' AND superseded_at IS NULL AND deleted_at IS NULL "
    "AND deleted_by IS NULL AND deletion_reason IS NULL) OR "
    "(status = 'SUPERSEDED' AND superseded_at IS NOT NULL AND deleted_at IS NULL "
    "AND deleted_by IS NULL AND deletion_reason IS NULL) OR "
    "(status = 'DELETED' AND superseded_at IS NULL AND deleted_at IS NOT NULL "
    "AND deleted_by IS NOT NULL AND deletion_reason IS NOT NULL)"
)


def upgrade() -> None:
    # Auxiliar sobre declaration_document_items (mismo patron que 0008 sobre
    # declarations): declaration_document_files necesita (id, tenant_id,
    # client_id) del item para su FK compuesta (ver ADR-0036 extendido).
    op.create_unique_constraint(
        "uq_declaration_document_items_id_tenant_client",
        "declaration_document_items",
        ["id", "tenant_id", "client_id"],
    )

    op.create_table(
        "declaration_document_files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("declaration_document_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(127), nullable=False),
        sa.Column("extension", sa.String(20), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("replaces_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deletion_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "client_id",
            "declaration_id",
            "declaration_document_item_id",
            "logical_file_id",
            name="uq_declaration_document_files_chain_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "logical_file_id", "version", name="uq_declaration_document_files_chain_version"
        ),
        sa.UniqueConstraint("storage_key", name="uq_declaration_document_files_storage_key"),
        sa.CheckConstraint(_FILE_STATUS_CHECK, name="ck_declaration_document_files_status"),
        sa.CheckConstraint("size_bytes > 0", name="ck_declaration_document_files_size_positive"),
        sa.CheckConstraint("version >= 1", name="ck_declaration_document_files_version_positive"),
        sa.CheckConstraint(_CHAIN_INTEGRITY_CHECK, name="ck_declaration_document_files_chain_integrity"),
        sa.CheckConstraint(
            _STATUS_COHERENCE_CHECK, name="ck_declaration_document_files_status_coherence"
        ),
        sa.ForeignKeyConstraint(
            ["declaration_document_item_id", "tenant_id", "client_id"],
            [
                "declaration_document_items.id",
                "declaration_document_items.tenant_id",
                "declaration_document_items.client_id",
            ],
            name="fk_declaration_document_files_item_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id", "client_id"],
            ["declarations.id", "declarations.tenant_id", "declarations.client_id"],
            name="fk_declaration_document_files_declaration_tenant_client",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["uploaded_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_document_files_uploaded_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_document_files_deleted_by_tenant",
        ),
    )

    op.create_index(
        "ix_declaration_document_files_tenant_item",
        "declaration_document_files",
        ["tenant_id", "declaration_document_item_id"],
    )
    op.create_index(
        "ix_declaration_document_files_tenant_status",
        "declaration_document_files",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_declaration_document_files_tenant_chain",
        "declaration_document_files",
        ["tenant_id", "logical_file_id"],
    )
    # Indice unico PARCIAL: solo una version ACTIVE por cadena a la vez (no
    # diferible -- ver Seccion 3/11 del plan). No se puede declarar dentro de
    # create_table (no soporta postgresql_where); se crea aparte, mismo
    # patron que los indices parciales de Bloque 2/3.2.
    op.create_index(
        "ux_declaration_document_files_chain_active",
        "declaration_document_files",
        ["tenant_id", "logical_file_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index("ux_declaration_document_files_chain_active", table_name="declaration_document_files")
    op.drop_index("ix_declaration_document_files_tenant_chain", table_name="declaration_document_files")
    op.drop_index("ix_declaration_document_files_tenant_status", table_name="declaration_document_files")
    op.drop_index("ix_declaration_document_files_tenant_item", table_name="declaration_document_files")
    op.drop_table("declaration_document_files")
    op.drop_constraint(
        "uq_declaration_document_items_id_tenant_client",
        "declaration_document_items",
        type_="unique",
    )
