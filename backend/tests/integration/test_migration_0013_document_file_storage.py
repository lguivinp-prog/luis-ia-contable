"""Matriz de migracion 0013 (almacenamiento real de archivos documentales,
Bloque 5): tabla nueva y vacia (sin preflight de datos, a diferencia de
0012) -- upgrade/downgrade/re-upgrade limpio, y verificacion de las
constraints/indices clave (cadena de version, indice unico parcial)."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config


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


def test_upgrade_creates_table_and_chain_constraints_then_downgrade_and_reupgrade(
    redirected_database_url,
) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0012")
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        assert _table_exists(engine, "declaration_document_files")
        assert _constraint_exists(
            engine, "declaration_document_items", "uq_declaration_document_items_id_tenant_client"
        )
        assert _constraint_exists(
            engine, "declaration_document_files", "uq_declaration_document_files_chain_scope"
        )
        assert _constraint_exists(
            engine, "declaration_document_files", "uq_declaration_document_files_chain_version"
        )
        assert _constraint_exists(
            engine, "declaration_document_files", "ck_declaration_document_files_chain_integrity"
        )
        assert _constraint_exists(
            engine, "declaration_document_files", "ck_declaration_document_files_status_coherence"
        )
        assert _constraint_exists(
            engine, "declaration_document_files", "fk_declaration_document_files_replaces_chain"
        )
        assert _index_exists(
            engine, "declaration_document_files", "ux_declaration_document_files_chain_active"
        )
    finally:
        engine.dispose()

    command.downgrade(cfg, "0012")
    engine = create_engine(url, future=True)
    try:
        assert not _table_exists(engine, "declaration_document_files")
        assert not _constraint_exists(
            engine, "declaration_document_items", "uq_declaration_document_items_id_tenant_client"
        )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert _table_exists(engine, "declaration_document_files")
    finally:
        engine.dispose()
