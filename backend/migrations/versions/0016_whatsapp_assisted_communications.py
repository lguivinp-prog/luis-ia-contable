"""Fase 3, Bloque 3.2: comunicaciones asistidas por WhatsApp (ver ADR-0067).
1 tabla nueva (communication_drafts): 1 FK simple + 5 FK compuestas + 1
UNIQUE + 6 CHECK + 4 indices. Ademas, agrega de forma no destructiva una
UNIQUE auxiliar sobre `declaration_document_files` (id, tenant_id,
client_id) -- necesaria porque la UNIQUE existente en esa tabla
(`uq_declaration_document_files_chain_scope`) incluye 6 columnas y no puede
usarse directamente como destino de una FK compuesta de 3 columnas.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-31

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_VALUES = ("DRAFT", "OPEN_REQUESTED", "MARKED_SENT", "DISCARDED")
_PHONE_E164_CHECK = r"recipient_phone_e164 ~ '^\+[1-9][0-9]{7,14}$'"

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


def upgrade() -> None:
    # --- UNIQUE auxiliar no destructiva sobre declaration_document_files ---
    # `id` ya es PK (globalmente unico), asi que esta UNIQUE mas amplia
    # siempre se satisface con los datos existentes -- nunca rechaza filas
    # preexistentes.
    op.create_unique_constraint(
        "uq_declaration_document_files_id_tenant_client",
        "declaration_document_files",
        ["id", "tenant_id", "client_id"],
    )

    # --- communication_drafts ---
    op.create_table(
        "communication_drafts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("related_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("related_document_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False, server_default="WHATSAPP"),
        sa.Column("template_code", sa.String(60), nullable=True),
        sa.Column("template_version", sa.Integer, nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("recipient_phone_display", sa.String(30), nullable=False),
        sa.Column("recipient_phone_e164", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("open_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("marked_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("marked_sent_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_communication_drafts_id_tenant"),
        sa.CheckConstraint("channel IN ('WHATSAPP')", name="ck_communication_drafts_channel"),
        sa.CheckConstraint(f"status IN {_STATUS_VALUES}", name="ck_communication_drafts_status"),
        sa.CheckConstraint("version >= 1", name="ck_communication_drafts_version_positive"),
        sa.CheckConstraint(
            "length(trim(body)) BETWEEN 1 AND 4000", name="ck_communication_drafts_body_length"
        ),
        sa.CheckConstraint(_PHONE_E164_CHECK, name="ck_communication_drafts_phone_e164_format"),
        sa.CheckConstraint(_STATUS_COHERENCE_CHECK, name="ck_communication_drafts_status_coherence"),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_communication_drafts_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_communication_drafts_created_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["marked_sent_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_communication_drafts_marked_sent_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["discarded_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_communication_drafts_discarded_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["related_task_id", "tenant_id"], ["operational_tasks.id", "operational_tasks.tenant_id"],
            name="fk_communication_drafts_related_task_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["related_document_file_id", "tenant_id", "client_id"],
            ["declaration_document_files.id", "declaration_document_files.tenant_id",
             "declaration_document_files.client_id"],
            name="fk_communication_drafts_related_document_tenant_client",
        ),
    )
    op.create_index(
        "ix_communication_drafts_tenant_client", "communication_drafts", ["tenant_id", "client_id"]
    )
    op.create_index(
        "ix_communication_drafts_tenant_status", "communication_drafts", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_communication_drafts_tenant_created_by", "communication_drafts", ["tenant_id", "created_by"]
    )
    op.create_index(
        "ix_communication_drafts_related_task",
        "communication_drafts",
        ["related_task_id"],
        postgresql_where=sa.text("related_task_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_communication_drafts_related_task", table_name="communication_drafts")
    op.drop_index("ix_communication_drafts_tenant_created_by", table_name="communication_drafts")
    op.drop_index("ix_communication_drafts_tenant_status", table_name="communication_drafts")
    op.drop_index("ix_communication_drafts_tenant_client", table_name="communication_drafts")
    op.drop_table("communication_drafts")

    op.drop_constraint(
        "uq_declaration_document_files_id_tenant_client",
        "declaration_document_files",
        type_="unique",
    )
