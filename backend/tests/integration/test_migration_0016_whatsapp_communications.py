"""Matriz de migracion 0016 (Fase 3 / Bloque 3.2, ver ADR-0067):
communication_drafts (1 FK simple + 5 FK compuestas + 1 UNIQUE + 6 CHECK)
mas la UNIQUE auxiliar no destructiva sobre declaration_document_files --
upgrade/downgrade/re-upgrade limpio + inspeccion de esquema completa (mismo
patron que test_migration_0015_google_oauth.py)."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config

_DRAFTS_UNIQUE = ["uq_communication_drafts_id_tenant"]

_DRAFTS_CHECKS = [
    "ck_communication_drafts_channel",
    "ck_communication_drafts_status",
    "ck_communication_drafts_version_positive",
    "ck_communication_drafts_body_length",
    "ck_communication_drafts_phone_e164_format",
    "ck_communication_drafts_status_coherence",
]

_DRAFTS_FKS = [
    "fk_communication_drafts_client_tenant",
    "fk_communication_drafts_created_by_tenant",
    "fk_communication_drafts_marked_sent_by_tenant",
    "fk_communication_drafts_discarded_by_tenant",
    "fk_communication_drafts_related_task_tenant",
    "fk_communication_drafts_related_document_tenant_client",
]

_DRAFTS_INDEXES = [
    "ix_communication_drafts_tenant_client",
    "ix_communication_drafts_tenant_status",
    "ix_communication_drafts_tenant_created_by",
    "ix_communication_drafts_related_task",
]


def _constraint_exists(engine, table: str, constraint: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = :table AND constraint_name = :constraint"
            ),
            {"table": table, "constraint": constraint},
        ).scalar()
    return result is not None


def _index_exists(engine, table: str, index: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT 1 FROM pg_indexes WHERE tablename = :table AND indexname = :index"
            ),
            {"table": table, "index": index},
        ).scalar()
    return result is not None


def _table_exists(engine, table: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :table"),
            {"table": table},
        ).scalar()
    return result is not None


def test_upgrade_creates_table_and_constraints_then_downgrade_and_reupgrade(
    redirected_database_url,
) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0015")
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        assert _table_exists(engine, "communication_drafts")

        for name in _DRAFTS_UNIQUE:
            assert _constraint_exists(engine, "communication_drafts", name), f"falta UNIQUE {name}"
        for name in _DRAFTS_CHECKS:
            assert _constraint_exists(engine, "communication_drafts", name), f"falta CHECK {name}"
        for name in _DRAFTS_FKS:
            assert _constraint_exists(engine, "communication_drafts", name), f"falta FK {name}"
        for name in _DRAFTS_INDEXES:
            assert _index_exists(engine, "communication_drafts", name), f"falta indice {name}"

        assert _constraint_exists(
            engine, "declaration_document_files", "uq_declaration_document_files_id_tenant_client"
        ), "falta la UNIQUE auxiliar sobre declaration_document_files"
    finally:
        engine.dispose()

    command.downgrade(cfg, "0015")
    engine = create_engine(url, future=True)
    try:
        assert not _table_exists(engine, "communication_drafts")
        assert not _constraint_exists(
            engine, "declaration_document_files", "uq_declaration_document_files_id_tenant_client"
        )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert _table_exists(engine, "communication_drafts")
        assert _constraint_exists(
            engine, "declaration_document_files", "uq_declaration_document_files_id_tenant_client"
        )
    finally:
        engine.dispose()
