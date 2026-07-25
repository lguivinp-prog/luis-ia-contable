"""CLI de sincronizacion con su propia transaccion (Fase 2, Bloque 1,
Seccion 18 del plan): abre su propia `Session`, hace un unico `commit()` si
todo termina bien, `rollback()` ante cualquier excepcion, y cierra la
sesion siempre. A diferencia de las pruebas que usan `db_session` (rollback
automatico de prueba), esta prueba commitea datos reales sobre `test_engine`
para poder verificarlos desde una sesion nueva e independiente -- igual que
`test_tax_payment_concurrency.py`."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.cli import sync_operational_tasks as cli
from app.core.security import hash_password
from app.models.operational_task import TaskGenerationRun
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


def _commit_tenant(session_factory) -> uuid.UUID:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant CLI sync")
        session.add(tenant)
        session.flush()
        session.add(
            User(
                tenant_id=tenant.id,
                email=f"cli-sync-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
                full_name="Usuario CLI",
                password_hash=hash_password("TestPassword123!"),
                role="admin",
                is_active=True,
            )
        )
        session.commit()
        return tenant.id
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.models.audit_log import AuditLog

    session = session_factory()
    try:
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def test_successful_run_persists_generation_run(session_factory, monkeypatch) -> None:
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    try:
        exit_code = cli.run(tenant_id)
        assert exit_code == 0

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
            assert runs[0].completed_at is not None
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, tenant_id)


def test_nonexistent_tenant_creates_no_rows_and_exits_nonzero(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    fake_tenant_id = uuid.uuid4()

    exit_code = cli.run(fake_tenant_id)
    assert exit_code == 1

    verify = session_factory()
    try:
        runs = (
            verify.execute(select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == fake_tenant_id))
            .scalars()
            .all()
        )
        assert runs == []
    finally:
        verify.close()


def test_forced_exception_rolls_back_everything(session_factory, monkeypatch) -> None:
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)

    def _boom(*args, **kwargs):
        raise RuntimeError("fallo forzado de prueba")

    monkeypatch.setattr(cli, "sync_operational_tasks", _boom)

    try:
        exit_code = cli.run(tenant_id)
        assert exit_code == 1

        verify = session_factory()
        try:
            runs = (
                verify.execute(select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
                .scalars()
                .all()
            )
            assert runs == []
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, tenant_id)


def test_second_run_is_idempotent(session_factory, monkeypatch) -> None:
    tenant_id = _commit_tenant(session_factory)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    try:
        assert cli.run(tenant_id) == 0
        assert cli.run(tenant_id) == 0

        verify = session_factory()
        try:
            runs = (
                verify.execute(select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
                .scalars()
                .all()
            )
            assert len(runs) == 2
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, tenant_id)
