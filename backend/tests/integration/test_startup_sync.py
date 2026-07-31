"""CLI de sincronizacion local de arranque (bloque transversal de operacion
local, ver ADR-0066): descubrimiento de tenants, transaccion independiente
por tenant, exclusion mutua por advisory lock de PostgreSQL, integraciones
externas nunca implementadas se omiten sin red y sin afectar el exit code.

Mismo patron que test_sync_operational_tasks_cli.py: el CLI abre sus propias
sesiones via `SessionLocal()` (module-level), asi que se monkeypatchea con
un `sessionmaker` real sobre `test_engine` para poder verificar datos
commiteados desde una sesion independiente."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.cli import startup_sync as cli
from app.cli.startup_sync import StartupSyncResult
from app.core.security import hash_password
from app.models.operational_task import TaskGenerationRun
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


def _commit_tenant(session_factory, *, tenant_id: uuid.UUID | None = None) -> uuid.UUID:
    session = session_factory()
    try:
        if tenant_id is None:
            tenant = Tenant(name="Tenant startup_sync")
        else:
            tenant = Tenant(id=tenant_id, name="Tenant startup_sync")
        session.add(tenant)
        session.flush()
        session.add(
            User(
                tenant_id=tenant.id,
                email=f"startup-sync-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
                full_name="Usuario startup_sync",
                password_hash=hash_password("TestPassword123!"),
                role="admin",
                is_active=True,
            )
        )
        session.commit()
        return tenant.id
    finally:
        session.close()


def _cleanup(session_factory, *tenant_ids: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.models.audit_log import AuditLog

    session = session_factory()
    try:
        for tenant_id in tenant_ids:
            session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
            session.execute(delete(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
            session.execute(delete(User).where(User.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def test_run_with_no_tenants(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(cli, "_discover_tenant_ids", lambda db: [])

    report = cli.run()

    assert report.lock_acquired is True
    assert report.tenants == []
    assert report.has_failures is False


def test_run_with_one_tenant_persists_generation_run(session_factory, monkeypatch) -> None:
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    try:
        report = cli.run()
        assert report.has_failures is False
        # No se asume que este sea el UNICO tenant en la base de datos
        # compartida de pruebas -- otros archivos de prueba tambien
        # commitean tenants reales (mismo patron de test_sync_operational_
        # tasks_cli.py). Solo se verifica que ESTE tenant fue procesado y
        # tuvo exito.
        mine = next(t for t in report.tenants if t.tenant_id == tenant_id)
        assert mine.succeeded is True

        verify = session_factory()
        try:
            runs = (
                verify.execute(select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
                .scalars()
                .all()
            )
            assert len(runs) == 1
            assert runs[0].trigger_source == "CLI"
            assert runs[0].triggered_by is None
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, tenant_id)


def test_run_processes_multiple_tenants_in_ascending_uuid_order(session_factory, monkeypatch) -> None:
    ids = [
        uuid.UUID("00000000-0000-0000-0000-000000000003"),
        uuid.UUID("00000000-0000-0000-0000-000000000001"),
        uuid.UUID("00000000-0000-0000-0000-000000000002"),
    ]
    for tid in ids:
        _commit_tenant(session_factory, tenant_id=tid)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    try:
        report = cli.run()
        processed_ids = [t.tenant_id for t in report.tenants if t.tenant_id in ids]
        assert processed_ids == sorted(ids)
    finally:
        _cleanup(session_factory, *ids)


def test_run_continues_after_one_tenant_fails(session_factory, monkeypatch) -> None:
    good_tenant = _commit_tenant(session_factory)
    bad_tenant = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)

    real_sync = cli.sync_operational_tasks

    def _selective_boom(db, tenant_id, *, trigger_source, triggered_by):
        if tenant_id == bad_tenant:
            raise RuntimeError("fallo forzado de prueba para un tenant")
        return real_sync(db, tenant_id, trigger_source=trigger_source, triggered_by=triggered_by)

    monkeypatch.setattr(cli, "sync_operational_tasks", _selective_boom)
    try:
        report = cli.run()
        assert report.has_failures is True

        by_id = {t.tenant_id: t for t in report.tenants}
        assert by_id[good_tenant].succeeded is True
        assert by_id[bad_tenant].succeeded is False
        assert by_id[bad_tenant].error is not None

        verify = session_factory()
        try:
            good_runs = (
                verify.execute(select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == good_tenant))
                .scalars()
                .all()
            )
            bad_runs = (
                verify.execute(select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == bad_tenant))
                .scalars()
                .all()
            )
            assert len(good_runs) == 1
            assert bad_runs == []  # transaccion del tenant fallido revertida por completo
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, good_tenant, bad_tenant)


def test_run_exit_code_nonzero_when_any_tenant_fails(session_factory, monkeypatch) -> None:
    bad_tenant = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)

    def _boom(*args, **kwargs):
        raise RuntimeError("fallo forzado")

    monkeypatch.setattr(cli, "sync_operational_tasks", _boom)
    try:
        report = cli.run()
        assert report.has_failures is True
        # replica exactamente la logica de main(): exit code 1 si hay fallos
        assert (1 if report.has_failures else 0) == 1
    finally:
        _cleanup(session_factory, bad_tenant)


def test_run_second_execution_is_idempotent_no_duplicate_tasks(session_factory, monkeypatch) -> None:
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    try:
        first = cli.run()
        second = cli.run()

        assert first.has_failures is False
        assert second.has_failures is False
        second_summary = next(t.summary for t in second.tenants if t.tenant_id == tenant_id)
        # nada tributario nuevo se creo entre la primera y la segunda corrida
        # -- la segunda no debe generar tareas duplicadas.
        assert second_summary["created"] == 0

        verify = session_factory()
        try:
            runs = (
                verify.execute(select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
                .scalars()
                .all()
            )
            assert len(runs) == 2  # dos ejecuciones registradas, sin tareas duplicadas
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, tenant_id)


def test_advisory_lock_prevents_concurrent_run(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    blocker = session_factory()
    try:
        # Sin commit intermedio: debe ser la misma conexion fisica la que
        # adquiere y (en el finally) libera el lock de sesion.
        acquired = blocker.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": cli.ADVISORY_LOCK_KEY}
        ).scalar()
        assert acquired is True

        report = cli.run()
        assert report.lock_acquired is False
        assert report.tenants == []
        assert report.has_failures is False
    finally:
        blocker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": cli.ADVISORY_LOCK_KEY})
        blocker.commit()
        blocker.close()


def test_lock_is_released_after_run_even_on_tenant_failure(session_factory, monkeypatch) -> None:
    bad_tenant = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)

    def _boom(*args, **kwargs):
        raise RuntimeError("fallo forzado")

    monkeypatch.setattr(cli, "sync_operational_tasks", _boom)
    try:
        cli.run()

        checker = session_factory()
        try:
            # Si el lock quedo liberado, una nueva sesion debe poder
            # re-adquirirlo de inmediato. OJO: nunca commitear entre adquirir
            # y liberar en la MISMA sesion -- un commit intermedio libera la
            # conexion fisica de vuelta al pool y la siguiente sentencia
            # podria ejecutarse sobre una conexion distinta, dejando el lock
            # huerfano (el mismo bug que este test verifica que no ocurra
            # dentro de startup_sync).
            reacquired = checker.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": cli.ADVISORY_LOCK_KEY}
            ).scalar()
            assert reacquired is True
            checker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": cli.ADVISORY_LOCK_KEY})
            checker.commit()
        finally:
            checker.close()
    finally:
        _cleanup(session_factory, bad_tenant)


def test_google_not_configured_reported_without_network(session_factory, monkeypatch) -> None:
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(cli, "is_google_oauth_configured", lambda: False)
    try:
        report = cli.run()
        assert report.google_oauth_configured is False
        assert report.integrations_skipped["google_calendar"] == "SKIPPED_NOT_IMPLEMENTED"
        assert report.integrations_skipped["google_drive"] == "SKIPPED_NOT_IMPLEMENTED"
        assert report.integrations_skipped["sunat_sol"] == "SKIPPED_NOT_IMPLEMENTED"
        assert report.integrations_skipped["gmail"] == "SKIPPED_NOT_IMPLEMENTED"
        assert report.integrations_skipped["whatsapp"] == "SKIPPED_NOT_IMPLEMENTED"
        assert report.has_failures is False
    finally:
        _cleanup(session_factory, tenant_id)


def test_missing_internet_never_blocks_local_sync(session_factory, monkeypatch) -> None:
    """No hay ninguna llamada de red en absoluto en esta ruta -- se
    simula "sin internet" reemplazando cualquier intento de red con una
    excepcion, y confirmando que la sincronizacion local sigue funcionando."""
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)

    import socket

    def _no_network(*args, **kwargs):
        raise OSError("red deshabilitada en esta prueba")

    monkeypatch.setattr(socket, "socket", _no_network)
    try:
        report = cli.run()
        assert report.has_failures is False
        # Igual que arriba: no se asume ser el unico tenant en la base
        # compartida de pruebas, solo que este tenant se sincronizo bien.
        mine = next(t for t in report.tenants if t.tenant_id == tenant_id)
        assert mine.succeeded is True
    finally:
        _cleanup(session_factory, tenant_id)


# --- Clasificacion de resultados (Seccion 4 de la ronda de correcciones) ---


def test_result_success_with_exit_code_zero(session_factory, monkeypatch) -> None:
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    try:
        report = cli.run()
        assert report.result == StartupSyncResult.SUCCESS
        assert report.exit_code == 0
        assert cli.EXIT_CODES[StartupSyncResult.SUCCESS] == 0
    finally:
        _cleanup(session_factory, tenant_id)


def test_result_skipped_locked_with_exit_code_zero(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    blocker = session_factory()
    try:
        acquired = blocker.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": cli.ADVISORY_LOCK_KEY}
        ).scalar()
        assert acquired is True

        report = cli.run()
        assert report.result == StartupSyncResult.SKIPPED_LOCKED
        assert report.exit_code == 0
        assert cli.EXIT_CODES[StartupSyncResult.SKIPPED_LOCKED] == 0
    finally:
        blocker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": cli.ADVISORY_LOCK_KEY})
        blocker.commit()
        blocker.close()


def test_result_partial_failure_with_exit_code_two(session_factory, monkeypatch) -> None:
    good_tenant = _commit_tenant(session_factory)
    bad_tenant = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)

    real_sync = cli.sync_operational_tasks

    def _selective_boom(db, tenant_id, *, trigger_source, triggered_by):
        if tenant_id == bad_tenant:
            raise RuntimeError("fallo forzado de un tenant")
        return real_sync(db, tenant_id, trigger_source=trigger_source, triggered_by=triggered_by)

    monkeypatch.setattr(cli, "sync_operational_tasks", _selective_boom)
    try:
        report = cli.run()
        assert report.result == StartupSyncResult.PARTIAL_FAILURE
        assert report.exit_code == 2
        assert cli.EXIT_CODES[StartupSyncResult.PARTIAL_FAILURE] == 2
        # El resultado parcial debe conservar conteos y el error normalizado
        # del tenant fallido -- nunca ocultar cual fallo.
        payload = report.to_json_dict()
        assert payload["tenants_failed"] == 1
        assert len(payload["failed_tenants"]) == 1
        assert payload["failed_tenants"][0]["tenant_id"] == str(bad_tenant)
        assert "fallo forzado" in payload["failed_tenants"][0]["error"]
    finally:
        _cleanup(session_factory, good_tenant, bad_tenant)


def test_result_fatal_failure_when_postgres_unreachable(monkeypatch) -> None:
    def _boom_session():
        raise OSError("simulado: PostgreSQL inalcanzable")

    monkeypatch.setattr(cli, "SessionLocal", _boom_session)
    report = cli.run()
    assert report.result == StartupSyncResult.FATAL_FAILURE
    assert report.exit_code == 1
    assert cli.EXIT_CODES[StartupSyncResult.FATAL_FAILURE] == 1
    assert report.fatal_error is not None
    assert "PostgreSQL" in report.fatal_error


def test_result_fatal_failure_when_advisory_lock_call_fails(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(
        cli,
        "_try_acquire_lock",
        lambda db: (_ for _ in ()).throw(RuntimeError("simulado: fallo del advisory lock")),
    )
    report = cli.run()
    assert report.result == StartupSyncResult.FATAL_FAILURE
    assert report.exit_code == 1
    assert "advisory lock" in report.fatal_error

    # El lock nunca debio quedar sostenido -- una sesion nueva debe poder
    # adquirirlo de inmediato.
    checker = session_factory()
    try:
        reacquired = checker.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": cli.ADVISORY_LOCK_KEY}
        ).scalar()
        assert reacquired is True
        checker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": cli.ADVISORY_LOCK_KEY})
        checker.commit()
    finally:
        checker.close()


def test_result_fatal_failure_when_tenant_discovery_fails(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(
        cli,
        "_discover_tenant_ids",
        lambda db: (_ for _ in ()).throw(RuntimeError("simulado: fallo al descubrir tenants")),
    )
    report = cli.run()
    assert report.result == StartupSyncResult.FATAL_FAILURE
    assert report.exit_code == 1
    assert "tenants" in report.fatal_error

    # El lock (ya adquirido antes del fallo de descubrimiento) debe quedar
    # liberado de todas formas.
    checker = session_factory()
    try:
        reacquired = checker.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": cli.ADVISORY_LOCK_KEY}
        ).scalar()
        assert reacquired is True
        checker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": cli.ADVISORY_LOCK_KEY})
        checker.commit()
    finally:
        checker.close()
