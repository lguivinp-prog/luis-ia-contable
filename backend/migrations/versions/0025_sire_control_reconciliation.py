"""Producto M4: expediente de control SIRE (RVIE/RCE) por contribuyente y periodo: fuentes versionadas (SUNAT vs DARWIN/CONTASIS), archivos de respaldo con SHA-256,
revisiones profesionales versionadas. Solo agrega tablas (ninguna fila existente cambia). El retroceso se BLOQUEA si existe algun expediente (no se destruye evidencia).

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels = None
depends_on = None

_SCOPE_COLS = ["control_id", "tenant_id", "client_id", "tax_period_id", "component"]
_SCOPE_REFS = ["sire_controls.id", "sire_controls.tenant_id", "sire_controls.client_id", "sire_controls.tax_period_id", "sire_controls.component"]
_UUID = postgresql.UUID(as_uuid=True)
_N = sa.Numeric(18, 2)


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("control_id", _UUID, nullable=False),
        sa.Column("client_id", _UUID, nullable=False),
        sa.Column("tax_period_id", _UUID, nullable=False),
        sa.Column("component", sa.String(4), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "sire_controls",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("client_id", _UUID, nullable=False),
        sa.Column("tax_period_id", _UUID, nullable=False),
        sa.Column("component", sa.String(4), nullable=False),
        sa.Column("tolerance_amount", _N, nullable=True),
        sa.Column("tolerance_reason", sa.Text(), nullable=True),
        sa.Column("tolerance_set_by", _UUID, nullable=True),
        sa.Column("tolerance_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "client_id", "tax_period_id", "component", name="uq_sire_controls_scope"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sire_controls_id_tenant"),
        sa.UniqueConstraint("id", "tenant_id", "client_id", "tax_period_id", "component", name="uq_sire_controls_id_scope"),
        sa.CheckConstraint("component IN ('RVIE', 'RCE')", name="ck_sire_controls_component"),
        sa.CheckConstraint("tolerance_amount IS NULL OR tolerance_amount >= 0", name="ck_sire_controls_tolerance_nonnegative"),
        sa.CheckConstraint(
            "(tolerance_amount IS NULL) OR (tolerance_reason IS NOT NULL AND length(btrim(tolerance_reason)) > 0 AND tolerance_set_by IS NOT NULL)",
            name="ck_sire_controls_tolerance_traceable",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_sire_controls_row_version_positive"),
        sa.ForeignKeyConstraint(["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_sire_controls_client_tenant"),
        sa.ForeignKeyConstraint(["tax_period_id", "tenant_id"], ["tax_periods.id", "tax_periods.tenant_id"], name="fk_sire_controls_period_tenant"),
        sa.ForeignKeyConstraint(["created_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_controls_created_by_tenant"),
    )
    op.create_index("ix_sire_controls_tenant_period", "sire_controls", ["tenant_id", "tax_period_id"])

    op.create_table(
        "sire_control_sources",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        *_scope_columns(),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("entry_mode", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("base_amount", _N, nullable=True),
        sa.Column("igv_amount", _N, nullable=True),
        sa.Column("total_amount", _N, nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source_label", sa.String(120), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("file_id", _UUID, nullable=True),
        sa.Column("import_meta", postgresql.JSONB(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", _UUID, nullable=True),
        sa.Column("replaces_source_id", _UUID, nullable=True),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("control_id", "origin", "version", name="uq_sire_control_sources_version"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sire_control_sources_id_tenant"),
        sa.ForeignKeyConstraint(_SCOPE_COLS, _SCOPE_REFS, name="fk_sire_control_sources_scope"),
        sa.ForeignKeyConstraint(["created_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_control_sources_created_by_tenant"),
        sa.CheckConstraint("origin IN ('SUNAT', 'DARWIN')", name="ck_sire_control_sources_origin"),
        sa.CheckConstraint("entry_mode IN ('MANUAL', 'IMPORT')", name="ck_sire_control_sources_entry_mode"),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED')", name="ck_sire_control_sources_status"),
        sa.CheckConstraint("currency IN ('PEN', 'USD')", name="ck_sire_control_sources_currency"),
        sa.CheckConstraint("version >= 1", name="ck_sire_control_sources_version_positive"),
        sa.CheckConstraint("record_count IS NULL OR record_count >= 0", name="ck_sire_control_sources_record_count"),
        sa.CheckConstraint(
            "record_count IS NOT NULL OR base_amount IS NOT NULL OR igv_amount IS NOT NULL OR total_amount IS NOT NULL",
            name="ck_sire_control_sources_has_data",
        ),
        sa.CheckConstraint("notes IS NULL OR length(notes) <= 2000", name="ck_sire_control_sources_notes_length"),
        sa.CheckConstraint("length(btrim(source_label)) > 0", name="ck_sire_control_sources_label_not_blank"),
    )
    op.create_index("ux_sire_control_sources_one_active", "sire_control_sources", ["control_id", "origin"], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"))
    op.create_index(
        "ux_sire_control_sources_idempotency", "sire_control_sources", ["tenant_id", "idempotency_key"], unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "sire_control_files",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        *_scope_columns(),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("purpose", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("source_id", _UUID, nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("extension", sa.String(10), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("uploaded_by", _UUID, nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sire_control_files_id_tenant"),
        sa.UniqueConstraint("storage_key", name="uq_sire_control_files_storage_key"),
        sa.ForeignKeyConstraint(_SCOPE_COLS, _SCOPE_REFS, name="fk_sire_control_files_scope"),
        sa.ForeignKeyConstraint(["uploaded_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_control_files_uploaded_by_tenant"),
        sa.CheckConstraint("origin IN ('SUNAT', 'DARWIN')", name="ck_sire_control_files_origin"),
        sa.CheckConstraint("status IN ('STAGED', 'ACTIVE', 'SUPERSEDED')", name="ck_sire_control_files_status"),
        sa.CheckConstraint("purpose IN ('SOURCE_FILE', 'EVIDENCE')", name="ck_sire_control_files_purpose"),
        sa.CheckConstraint("size_bytes > 0", name="ck_sire_control_files_size_positive"),
        sa.CheckConstraint("length(sha256_hash) = 64", name="ck_sire_control_files_sha256_length"),
    )
    op.create_index(
        "ux_sire_control_files_dedupe", "sire_control_files", ["control_id", "origin", "sha256_hash"], unique=True,
        postgresql_where=sa.text("status IN ('STAGED', 'ACTIVE')"),
    )

    op.create_table(
        "sire_control_reviews",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        *_scope_columns(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("comparison_hash", sa.String(64), nullable=False),
        sa.Column("comparison_status", sa.String(20), nullable=False),
        sa.Column("comparison_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("sunat_source_id", _UUID, nullable=True),
        sa.Column("darwin_source_id", _UUID, nullable=True),
        sa.Column("idempotency_key", _UUID, nullable=True),
        sa.Column("reviewed_by", _UUID, nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("control_id", "version", name="uq_sire_control_reviews_version"),
        sa.ForeignKeyConstraint(_SCOPE_COLS, _SCOPE_REFS, name="fk_sire_control_reviews_scope"),
        sa.ForeignKeyConstraint(["reviewed_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_control_reviews_reviewer_tenant"),
        sa.CheckConstraint("decision IN ('APPROVED', 'OBSERVED', 'REQUIRES_REGULARIZATION')", name="ck_sire_control_reviews_decision"),
        sa.CheckConstraint("status IN ('CURRENT', 'SUPERSEDED')", name="ck_sire_control_reviews_status"),
        sa.CheckConstraint("version >= 1", name="ck_sire_control_reviews_version_positive"),
        sa.CheckConstraint("notes IS NULL OR length(notes) <= 2000", name="ck_sire_control_reviews_notes_length"),
        sa.CheckConstraint("decision = 'APPROVED' OR (notes IS NOT NULL AND length(btrim(notes)) > 0)", name="ck_sire_control_reviews_notes_required"),
    )
    op.create_index("ux_sire_control_reviews_one_current", "sire_control_reviews", ["control_id"], unique=True, postgresql_where=sa.text("status = 'CURRENT'"))
    op.create_index(
        "ux_sire_control_reviews_idempotency", "sire_control_reviews", ["tenant_id", "idempotency_key"], unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(text("SELECT count(*) FROM sire_controls")).scalar_one()
    if existing:
        raise RuntimeError(f"downgrade 0025 bloqueado: existen {existing} expediente(s) de control SIRE (evidencia que no se destruye)")
    for name in ("ux_sire_control_reviews_idempotency", "ux_sire_control_reviews_one_current"):
        op.drop_index(name, table_name="sire_control_reviews")
    op.drop_table("sire_control_reviews")
    op.drop_index("ux_sire_control_files_dedupe", table_name="sire_control_files")
    op.drop_table("sire_control_files")
    for name in ("ux_sire_control_sources_idempotency", "ux_sire_control_sources_one_active"):
        op.drop_index(name, table_name="sire_control_sources")
    op.drop_table("sire_control_sources")
    op.drop_index("ix_sire_controls_tenant_period", table_name="sire_controls")
    op.drop_table("sire_controls")
