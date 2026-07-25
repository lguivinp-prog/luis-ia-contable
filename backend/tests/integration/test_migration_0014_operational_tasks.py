"""Matriz de migracion 0014 (gestion operativa de tareas, Fase 2/Bloque 1):
4 tablas nuevas, 11 FK compuestas + 7 CHECK en operational_tasks, 4
constraints auxiliares en tablas preexistentes, 2 UNIQUE(id, tenant_id)
internas -- upgrade/downgrade/re-upgrade limpio + inspeccion de esquema
completa (mismo patron que test_migration_0013_document_file_storage.py)."""

from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.models.operational_task import OperationalTask, TaskGenerationRun
from tests.integration._declaration_helpers import make_other_tenant_with_user
from tests.integration._migration_helpers import make_alembic_config
from tests.integration._operational_task_helpers import make_declaration_setup

_DUE = datetime(2026, 2, 12, 23, 59, 59, tzinfo=UTC)

_OPERATIONAL_TASKS_FKS = [
    "fk_operational_tasks_client_tenant",
    "fk_operational_tasks_declaration_tenant_client",
    "fk_operational_tasks_document_item_tenant_client_declaration",
    "fk_operational_tasks_fee_charge_tenant_agreement",
    "fk_operational_tasks_fee_agreement_tenant_client",
    "fk_operational_tasks_tax_payment_tenant_declaration",
    "fk_operational_tasks_assigned_to_tenant",
    "fk_operational_tasks_created_by_tenant",
    "fk_operational_tasks_completed_by_tenant",
    "fk_operational_tasks_cancelled_by_tenant",
    "fk_operational_tasks_generation_run_tenant",
]

_OPERATIONAL_TASKS_CHECKS = [
    "ck_operational_tasks_task_type",
    "ck_operational_tasks_status",
    "ck_operational_tasks_priority",
    "ck_operational_tasks_version_positive",
    "ck_operational_tasks_fee_pair_coherence",
    "ck_operational_tasks_source_shape",
    "ck_operational_tasks_client_required_with_anchor",
    "ck_operational_tasks_generated_source_matches_type",
    "ck_operational_tasks_generation_coherence",
    "ck_operational_tasks_source_due_coherence",
    "ck_operational_tasks_status_resolution_coherence",
    "ck_operational_tasks_starts_before_due",
]

_TASK_CHANGE_HISTORY_FKS = [
    "fk_task_change_history_task_tenant",
    "fk_task_change_history_actor_tenant",
    "fk_task_change_history_from_assigned_to_tenant",
    "fk_task_change_history_to_assigned_to_tenant",
]

_TASK_CHANGE_HISTORY_CHECKS = [
    "ck_task_change_history_event_type",
    "ck_task_change_history_origin_actor_coherence",
    "ck_task_change_history_non_empty",
    "ck_tch_created_complete",
    "ck_tch_status_events_have_status",
    "ck_tch_assigned_has_change",
    "ck_tch_rescheduled_has_change",
    "ck_tch_source_due_has_change",
    "ck_tch_source_resolution_has_status",
    "ck_tch_updated_has_content_change",
]

_TASK_COMMENTS_FKS = ["fk_task_comments_task_tenant", "fk_task_comments_author_tenant"]

_AUXILIARY_CONSTRAINTS = [
    ("declaration_document_items", "uq_declaration_document_items_id_tenant_client_declaration"),
    ("tax_payments", "uq_tax_payments_id_tenant_declaration"),
    ("professional_fee_charges", "uq_professional_fee_charges_id_tenant_agreement"),
    ("professional_fee_agreements", "uq_professional_fee_agreements_id_tenant_client"),
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


def _table_exists(engine, table: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :table"),
            {"table": table},
        ).scalar()
    return result is not None


def _unique_index_info(engine, table: str, index_name: str) -> dict | None:
    """Inspecciona un indice por `pg_index`/`pg_class` -- a diferencia de
    `information_schema.table_constraints`, este es el unico camino real
    para inspeccionar un indice unico PARCIAL (creado via `CREATE INDEX ...
    WHERE ...`, no via `ADD CONSTRAINT`), que nunca aparece como
    "constraint" en el catalogo ANSI ni en `pg_constraint`."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    ix.indisunique AS is_unique,
                    (ix.indpred IS NOT NULL) AS is_partial,
                    pg_get_expr(ix.indpred, ix.indrelid) AS predicate,
                    array_agg(a.attname ORDER BY k.ordinality) AS columns
                FROM pg_class t
                JOIN pg_index ix ON ix.indrelid = t.oid
                JOIN pg_class i ON i.oid = ix.indexrelid
                JOIN unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON true
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                WHERE t.relname = :table AND i.relname = :index_name
                GROUP BY ix.indisunique, ix.indpred, ix.indrelid
                """
            ),
            {"table": table, "index_name": index_name},
        ).mappings().first()
    return dict(row) if row else None


def _full_unique_constraint_exists_on_columns(engine, table: str, columns: list[str]) -> bool:
    """True si existe un `UNIQUE` (contype='u' en `pg_constraint`, NO un
    indice parcial) cubriendo exactamente estas columnas -- usado para
    confirmar que NUNCA coexisten un `UniqueConstraint` completo y el
    indice parcial equivalente sobre las mismas columnas."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.conname, array_agg(a.attname ORDER BY k.ordinality) AS cols
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON true
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                WHERE t.relname = :table AND c.contype = 'u'
                GROUP BY c.conname
                """
            ),
            {"table": table},
        ).all()
    return any(set(cols) == set(columns) for _, cols in rows)


def test_upgrade_creates_all_tables_and_constraints_then_downgrade_and_reupgrade(
    redirected_database_url,
) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0013")
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        for table in ("task_generation_runs", "operational_tasks", "task_change_history", "task_comments"):
            assert _table_exists(engine, table), f"tabla faltante: {table}"

        assert _constraint_exists(engine, "operational_tasks", "uq_operational_tasks_id_tenant")
        # `uq_operational_tasks_tenant_generation_key` es un indice unico
        # PARCIAL (CREATE INDEX ... WHERE generation_key IS NOT NULL), no un
        # UniqueConstraint -- nunca aparece en information_schema.table_
        # constraints/pg_constraint, solo en pg_index.
        index_info = _unique_index_info(
            engine, "operational_tasks", "uq_operational_tasks_tenant_generation_key"
        )
        assert index_info is not None, "indice uq_operational_tasks_tenant_generation_key faltante"
        assert index_info["is_unique"] is True
        assert index_info["columns"] == ["tenant_id", "generation_key"]
        assert index_info["is_partial"] is True
        # pg_get_expr envuelve la condicion entre parentesis
        # ("(generation_key IS NOT NULL)") -- se compara la condicion
        # normalizada, equivalente a la exigida literalmente.
        assert index_info["predicate"].strip("()") == "generation_key IS NOT NULL"
        # Nunca debe coexistir un UniqueConstraint completo sobre las mismas
        # columnas -- una sola estructura de unicidad: el indice parcial.
        assert not _full_unique_constraint_exists_on_columns(
            engine, "operational_tasks", ["tenant_id", "generation_key"]
        )
        assert _constraint_exists(
            engine, "task_generation_runs", "uq_task_generation_runs_id_tenant"
        )

        for fk in _OPERATIONAL_TASKS_FKS:
            assert _constraint_exists(engine, "operational_tasks", fk), f"FK faltante: {fk}"
        for check in _OPERATIONAL_TASKS_CHECKS:
            assert _constraint_exists(engine, "operational_tasks", check), f"CHECK faltante: {check}"
        for fk in _TASK_CHANGE_HISTORY_FKS:
            assert _constraint_exists(engine, "task_change_history", fk), f"FK faltante: {fk}"
        for check in _TASK_CHANGE_HISTORY_CHECKS:
            assert _constraint_exists(engine, "task_change_history", check), f"CHECK faltante: {check}"
        for fk in _TASK_COMMENTS_FKS:
            assert _constraint_exists(engine, "task_comments", fk), f"FK faltante: {fk}"
        assert _constraint_exists(engine, "task_comments", "ck_task_comments_body_length")

        for table, constraint in _AUXILIARY_CONSTRAINTS:
            assert _constraint_exists(
                engine, table, constraint
            ), f"constraint auxiliar faltante: {constraint}"
    finally:
        engine.dispose()

    command.downgrade(cfg, "0013")
    engine = create_engine(url, future=True)
    try:
        for table in ("task_comments", "task_change_history", "operational_tasks", "task_generation_runs"):
            assert not _table_exists(engine, table), f"tabla no eliminada en downgrade: {table}"
        for table, constraint in _AUXILIARY_CONSTRAINTS:
            assert not _constraint_exists(
                engine, table, constraint
            ), f"constraint auxiliar no eliminada en downgrade: {constraint}"
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert _table_exists(engine, "operational_tasks")
    finally:
        engine.dispose()


# --- comportamiento real del indice unico parcial (datos, no solo esquema) ---
# Estas pruebas corren contra `test_engine` (schema generado por
# `Base.metadata.create_all()` desde los modelos reales, igual que el resto
# de la suite) -- confirman que el modelo `OperationalTask` actualizado se
# comporta exactamente como el indice parcial creado por la migracion 0014.


def _generated_task(tenant, client, declaration, run, *, generation_key: str) -> OperationalTask:
    return OperationalTask(
        tenant_id=tenant.id,
        client_id=client.id,
        declaration_id=declaration.id,
        title="Presentar declaracion",
        description=None,
        task_type="DECLARATION_FILING",
        priority="HIGH",
        status="PENDING",
        due_at=_DUE,
        due_has_time=False,
        source_due_at=_DUE,
        due_is_overridden=False,
        generation_key=generation_key,
        is_system_generated=True,
        generation_run_id=run.id,
        created_by=None,
    )


def _generation_run(tenant, admin_user) -> TaskGenerationRun:
    now = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    return TaskGenerationRun(
        tenant_id=tenant.id,
        triggered_by=admin_user.id,
        trigger_source="MANUAL",
        started_at=now,
        completed_at=now,
        summary={},
    )


def test_multiple_manual_tasks_with_null_generation_key_are_valid(
    db_session, tenant, admin_user
) -> None:
    """El indice parcial (`WHERE generation_key IS NOT NULL`) nunca evalua
    filas con `generation_key IS NULL` -- multiples tareas manuales sin
    `generation_key` bajo el mismo tenant deben insertarse sin conflicto,
    a diferencia de un `UniqueConstraint` completo (que trataria varios
    `NULL` igual, pero aqui ni siquiera llega a evaluarlos)."""
    for i in range(3):
        task = OperationalTask(
            tenant_id=tenant.id,
            title=f"Tarea manual {i}",
            task_type="GENERAL",
            priority="NORMAL",
            status="PENDING",
            created_by=admin_user.id,
            due_at=_DUE,
            is_system_generated=False,
        )
        db_session.add(task)
    db_session.flush()

    count = (
        db_session.query(OperationalTask)
        .filter(OperationalTask.tenant_id == tenant.id, OperationalTask.generation_key.is_(None))
        .count()
    )
    assert count == 3


def test_same_generation_key_same_tenant_rejected_or_resolved_via_on_conflict(
    db_session, tenant, admin_user, obligation_catalog
) -> None:
    """Dos tareas generadas del MISMO tenant con la MISMA `generation_key`
    no nula: un intento de insercion ORM directa (fuera del camino
    idempotente `ON CONFLICT DO NOTHING` del servicio real) debe ser
    rechazado por el indice unico parcial con `IntegrityError`."""
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    run = _generation_run(tenant, admin_user)
    db_session.add(run)
    db_session.flush()

    key = f"DECL_FILING:{setup['declaration'].id}"
    db_session.add(_generated_task(tenant, setup["client"], setup["declaration"], run, generation_key=key))
    db_session.flush()

    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(
            _generated_task(tenant, setup["client"], setup["declaration"], run, generation_key=key)
        )
        db_session.flush()
    savepoint.rollback()

    count = (
        db_session.query(OperationalTask)
        .filter(OperationalTask.tenant_id == tenant.id, OperationalTask.generation_key == key)
        .count()
    )
    assert count == 1, "la segunda insercion directa debia ser rechazada, sin duplicar"


def test_same_generation_key_different_tenants_is_valid(
    db_session, tenant, admin_user, obligation_catalog
) -> None:
    """El indice parcial es `UNIQUE (tenant_id, generation_key) WHERE
    generation_key IS NOT NULL` -- la MISMA `generation_key` en tenants
    DISTINTOS es valida porque `tenant_id` difiere; nunca colisiona."""
    other_tenant, other_admin = make_other_tenant_with_user(db_session)

    setup_a = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    run_a = _generation_run(tenant, admin_user)
    db_session.add(run_a)
    db_session.flush()

    setup_b = make_declaration_setup(db_session, other_tenant, other_admin, obligation_catalog)
    run_b = _generation_run(other_tenant, other_admin)
    db_session.add(run_b)
    db_session.flush()

    shared_key = "DECL_FILING:shared-across-tenants"
    db_session.add(
        _generated_task(tenant, setup_a["client"], setup_a["declaration"], run_a, generation_key=shared_key)
    )
    db_session.add(
        _generated_task(
            other_tenant, setup_b["client"], setup_b["declaration"], run_b, generation_key=shared_key
        )
    )
    db_session.flush()

    count = (
        db_session.query(OperationalTask).filter(OperationalTask.generation_key == shared_key).count()
    )
    assert count == 2, "la misma generation_key en tenants distintos debe convivir sin conflicto"
