"""Tax schedule engine: sources, versions, entries, exceptions, approval history, import runs

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIGITS_RANGE_CHECK = (
    "applicable_last_digits IS NULL OR "
    "applicable_last_digits <@ ARRAY[0,1,2,3,4,5,6,7,8,9]::smallint[]"
)


def upgrade() -> None:
    op.create_table(
        "tax_schedule_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "obligation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("obligation_catalog.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("reference_url", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # tax_schedule_versions <-> tax_schedule_import_runs tienen FKs mutuas.
    # Se crea "versions" primero sin la FK de import_run_id (se agrega luego
    # de crear "import_runs"), para evitar el ciclo en el CREATE TABLE.
    op.create_table(
        "tax_schedule_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_sources.id"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(50), nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger, nullable=False),
        sa.Column("schedule_type", sa.String(30), nullable=False, server_default="GENERAL"),
        sa.Column(
            "scope_category_code",
            sa.String(50),
            sa.ForeignKey("tax_category_catalog.code"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("normative_reference", sa.Text, nullable=True),
        sa.Column("source_document_url", sa.Text, nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("import_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "superseded_by_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_versions.id"),
            nullable=True,
        ),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "tax_schedule_import_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_sources.id"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_versions.id"),
            nullable=True,
        ),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_checksum", sa.String(64), nullable=False),
        sa.Column("raw_content", sa.Text, nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger, nullable=False),
        sa.Column("schedule_type", sa.String(30), nullable=False, server_default="GENERAL"),
        sa.Column(
            "scope_category_code",
            sa.String(50),
            sa.ForeignKey("tax_category_catalog.code"),
            nullable=True,
        ),
        sa.Column("parsed_rows", postgresql.JSONB, nullable=True),
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="RECEIVED"),
        sa.Column("validation_errors", postgresql.JSONB, nullable=True),
        sa.Column("diff_summary", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
    )

    op.create_foreign_key(
        "fk_tax_schedule_versions_import_run_id",
        "tax_schedule_versions",
        "tax_schedule_import_runs",
        ["import_run_id"],
        ["id"],
    )

    op.create_index(
        "ux_tax_schedule_versions_one_approved_per_scope",
        "tax_schedule_versions",
        [
            "tenant_id",
            "source_id",
            "fiscal_year",
            "schedule_type",
            sa.text("COALESCE(scope_category_code, '')"),
        ],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )

    op.create_table(
        "tax_schedule_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_versions.id"),
            nullable=False,
        ),
        sa.Column("tax_period", sa.String(7), nullable=False),
        sa.Column("applicable_last_digits", postgresql.ARRAY(sa.SmallInteger), nullable=True),
        sa.Column(
            "required_category_code",
            sa.String(50),
            sa.ForeignKey("tax_category_catalog.code"),
            nullable=True,
        ),
        sa.Column("priority", sa.SmallInteger, nullable=True),
        sa.Column("due_date_original", sa.Date, nullable=False),
        sa.Column("due_date_effective", sa.Date, nullable=False),
        sa.Column("due_date_type", sa.String(30), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_DIGITS_RANGE_CHECK, name="ck_tax_schedule_entries_digits_range"),
    )

    op.create_table(
        "tax_schedule_exceptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_versions.id"),
            nullable=False,
        ),
        sa.Column("tax_period", sa.String(7), nullable=False),
        sa.Column("applicable_last_digits", postgresql.ARRAY(sa.SmallInteger), nullable=True),
        sa.Column(
            "required_category_code",
            sa.String(50),
            sa.ForeignKey("tax_category_catalog.code"),
            nullable=True,
        ),
        sa.Column("priority", sa.SmallInteger, nullable=True),
        sa.Column("due_date_effective", sa.Date, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_DIGITS_RANGE_CHECK, name="ck_tax_schedule_exceptions_digits_range"),
    )

    op.create_table(
        "tax_schedule_approval_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_versions.id"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column(
            "actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tax_schedule_approval_history")
    op.drop_table("tax_schedule_exceptions")
    op.drop_table("tax_schedule_entries")
    op.drop_index("ux_tax_schedule_versions_one_approved_per_scope", table_name="tax_schedule_versions")
    op.drop_constraint(
        "fk_tax_schedule_versions_import_run_id", "tax_schedule_versions", type_="foreignkey"
    )
    op.drop_table("tax_schedule_import_runs")
    op.drop_table("tax_schedule_versions")
    op.drop_table("tax_schedule_sources")
