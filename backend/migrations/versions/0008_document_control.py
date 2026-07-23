"""Document control: document_requirement_catalog, client_document_requirements
(client_obligation_id real), declaration_document_items (client_id + cross-client
integrity), document_item_status_history

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-23

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATALOG_SCOPE_CHECK = (
    "(is_system = true AND tenant_id IS NULL) OR (is_system = false AND tenant_id IS NOT NULL)"
)

_ITEM_STATUS_CHECK = (
    "status IN ('NOT_REQUESTED', 'REQUESTED', 'PARTIAL', 'RECEIVED', 'OBSERVED', 'ACCEPTED', "
    "'NOT_APPLICABLE')"
)


def upgrade() -> None:
    # Auxiliar sobre la tabla de 3.1 (mismo patron que 0006 tocando tablas de
    # Bloque 1/2): declaration_document_items necesita (id, tenant_id, client_id)
    # de "declarations" para su FK compuesta (ver ADR-0036).
    op.create_unique_constraint(
        "uq_declarations_id_tenant_client", "declarations", ["id", "tenant_id", "client_id"]
    )

    op.create_table(
        "document_requirement_catalog",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=True
        ),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(30), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_CATALOG_SCOPE_CHECK, name="ck_document_requirement_catalog_scope"),
    )
    op.create_index(
        "ux_document_requirement_catalog_system_code",
        "document_requirement_catalog",
        ["code"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )
    op.create_index(
        "ux_document_requirement_catalog_tenant_code",
        "document_requirement_catalog",
        ["tenant_id", "code"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )

    op.create_table(
        "client_document_requirements",
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
        sa.Column(
            "requirement_catalog_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_requirement_catalog.id"),
            nullable=False,
        ),
        sa.Column("client_obligation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_mandatory", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("periodicity", sa.String(20), nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("responsible_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reminder_lead_days", sa.SmallInteger, nullable=False, server_default="5"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "id", "tenant_id", "client_id", name="uq_client_document_requirements_id_tenant_client"
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_client_document_requirements_valid_range",
        ),
        sa.CheckConstraint(
            "reminder_lead_days >= 0", name="ck_client_document_requirements_lead_days"
        ),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_client_document_requirements_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["client_obligation_id", "tenant_id", "client_id"],
            ["client_obligations.id", "client_obligations.tenant_id", "client_obligations.client_id"],
            name="fk_client_document_requirements_obligation_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_document_requirements_responsible_tenant",
        ),
    )
    # Columna generada (no mapeada como escribible en el ORM) + EXCLUDE (misma
    # tecnica que 0003_tax_categories / plan de 3.1).
    op.execute(
        "ALTER TABLE client_document_requirements "
        "ADD COLUMN validity_range daterange "
        "GENERATED ALWAYS AS (daterange(valid_from, valid_until, '[]')) STORED"
    )
    op.execute(
        "ALTER TABLE client_document_requirements "
        "ADD CONSTRAINT ex_client_document_requirements_no_overlap "
        "EXCLUDE USING gist ("
        "tenant_id WITH =, "
        "client_id WITH =, "
        "requirement_catalog_id WITH =, "
        "COALESCE(client_obligation_id::text, '') WITH =, "
        "COALESCE(periodicity, '') WITH =, "
        "validity_range WITH &&"
        ")"
    )

    op.create_table(
        "declaration_document_items",
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
        sa.Column("client_document_requirement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="NOT_REQUESTED"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column("responsible_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reminder_count", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_declaration_document_items_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "declaration_id", "client_document_requirement_id",
            name="uq_declaration_document_items_declaration_requirement",
        ),
        sa.CheckConstraint(_ITEM_STATUS_CHECK, name="ck_declaration_document_items_status"),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id", "client_id"],
            ["declarations.id", "declarations.tenant_id", "declarations.client_id"],
            name="fk_declaration_document_items_declaration_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["client_document_requirement_id", "tenant_id", "client_id"],
            [
                "client_document_requirements.id",
                "client_document_requirements.tenant_id",
                "client_document_requirements.client_id",
            ],
            name="fk_declaration_document_items_requirement_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_document_items_responsible_tenant",
        ),
    )

    op.create_table(
        "document_item_status_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("declaration_document_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(
            ["declaration_document_item_id", "tenant_id"],
            ["declaration_document_items.id", "declaration_document_items.tenant_id"],
            name="fk_document_item_status_history_item_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_document_item_status_history_actor_tenant",
        ),
    )


def downgrade() -> None:
    op.drop_table("document_item_status_history")
    op.drop_table("declaration_document_items")
    op.execute(
        "ALTER TABLE client_document_requirements DROP CONSTRAINT ex_client_document_requirements_no_overlap"
    )
    op.drop_table("client_document_requirements")
    op.drop_index("ux_document_requirement_catalog_tenant_code", table_name="document_requirement_catalog")
    op.drop_index("ux_document_requirement_catalog_system_code", table_name="document_requirement_catalog")
    op.drop_table("document_requirement_catalog")
    op.drop_constraint("uq_declarations_id_tenant_client", "declarations", type_="unique")
