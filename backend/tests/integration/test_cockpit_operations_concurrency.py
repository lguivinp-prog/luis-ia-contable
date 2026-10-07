"""Concurrencia real (dos conexiones independientes) de los estados operativos manuales del cockpit: con la misma
`expected_row_version` solo UNA escritura gana; la otra recibe conflicto y nada se pierde ni se duplica."""

import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.client_period_operation import ClientPeriodOperation, ClientPeriodOperationHistory
from app.models.tax_period import TaxPeriod
from app.models.tenant import Tenant
from app.models.user import User
from app.services import cockpit_operations_service as service


@pytest.fixture()
def committed_scope(test_engine):
    factory = sessionmaker(bind=test_engine, future=True)
    session = factory()
    tenant = Tenant(name="Tenant concurrencia cockpit")
    session.add(tenant)
    session.flush()
    user = User(
        tenant_id=tenant.id, email=f"cockpit-conc-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        full_name="Admin", password_hash=hash_password("TestPassword123!"), role="admin", is_active=True,
    )
    client = Client(
        tenant_id=tenant.id, ruc="20" + str(uuid.uuid4().int)[:9], business_name="C", person_type="juridica",
        ruc_status="ACTIVO", fiscal_address_condition="HABIDO", tax_regime="GENERAL", service_status="activo",
    )
    session.add_all([user, client])
    session.flush()
    period = TaxPeriod(
        tenant_id=tenant.id, period_type="MONTHLY", fiscal_year=2026, month=1, canonical_code="2026-01", start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31), is_open=True,
    )
    session.add(period)
    session.commit()
    ids = {"tenant": tenant.id, "user": user.id, "client": client.id, "period": period.id}
    session.close()
    yield factory, ids
    cleanup = factory()
    for model in (ClientPeriodOperationHistory, ClientPeriodOperation, AuditLog):
        cleanup.execute(delete(model).where(model.tenant_id == ids["tenant"]))
    cleanup.execute(delete(TaxPeriod).where(TaxPeriod.tenant_id == ids["tenant"]))
    cleanup.execute(delete(Client).where(Client.tenant_id == ids["tenant"]))
    cleanup.execute(delete(User).where(User.tenant_id == ids["tenant"]))
    cleanup.execute(delete(Tenant).where(Tenant.id == ids["tenant"]))
    cleanup.commit()
    cleanup.close()


def _update(factory, ids, changes, expected, gate=None, ready=None):
    session = factory()
    try:
        if ready is not None:
            ready.set()
        if gate is not None:
            gate.wait(timeout=10)
        state = service.update_operations(
            session, ids["tenant"], ids["user"], ids["client"], ids["period"], changes=changes, expected_row_version=expected,
        )
        session.commit()
        return ("ok", state.row_version)
    except service.OperationsVersionConflictError as exc:
        session.rollback()
        return ("conflict", exc.current_row_version)
    finally:
        session.close()


def _race(factory, ids, expected, changes_a, changes_b):
    gate = threading.Event()
    results: dict[str, tuple] = {}
    ready = [threading.Event(), threading.Event()]

    def worker(name, changes, flag):
        results[name] = _update(factory, ids, changes, expected, gate=gate, ready=flag)

    threads = [
        threading.Thread(target=worker, args=("a", changes_a, ready[0])),
        threading.Thread(target=worker, args=("b", changes_b, ready[1])),
    ]
    for t in threads:
        t.start()
    for flag in ready:
        assert flag.wait(timeout=10)
    gate.set()
    for t in threads:
        t.join(timeout=20)
        assert not t.is_alive()
    return results


def _totals(factory, ids):
    session = factory()
    try:
        ops = session.execute(
            select(func.count()).select_from(ClientPeriodOperation).where(ClientPeriodOperation.tenant_id == ids["tenant"])
        ).scalar_one()
        history = session.execute(
            select(func.count()).select_from(ClientPeriodOperationHistory).where(
                ClientPeriodOperationHistory.tenant_id == ids["tenant"]
            )
        ).scalar_one()
        return ops, history
    finally:
        session.close()


def test_concurrent_lazy_creation_has_one_winner_and_one_row(committed_scope):
    factory, ids = committed_scope
    results = _race(factory, ids, 0, {"buzon_status": "REVIEWED_CLEAR"}, {"accounting_status": "COMPLETED"})
    outcomes = sorted(r[0] for r in results.values())
    assert outcomes == ["conflict", "ok"], results
    assert next(r for r in results.values() if r[0] == "conflict")[1] == 1
    assert _totals(factory, ids) == (1, 1)


def test_concurrent_update_of_existing_row_has_one_winner(committed_scope):
    factory, ids = committed_scope
    assert _update(factory, ids, {"notes": "inicial"}, 0) == ("ok", 1)
    results = _race(factory, ids, 1, {"buzon_status": "REVIEWED_CLEAR"}, {"buzon_status": "REVIEWED_ACTION_REQUIRED"})
    assert sorted(r[0] for r in results.values()) == ["conflict", "ok"], results
    session = factory()
    try:
        row = session.execute(select(ClientPeriodOperation).where(ClientPeriodOperation.tenant_id == ids["tenant"])).scalar_one()
        assert row.row_version == 2
        assert row.buzon_status in {"REVIEWED_CLEAR", "REVIEWED_ACTION_REQUIRED"}
    finally:
        session.close()
    assert _totals(factory, ids) == (1, 2)  # notas + un unico cambio de Buzon
