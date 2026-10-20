"""M4: concurrencia REAL (conexiones independientes) del expediente SIRE: una sola version por doble clic, un solo ganador por reemplazo, una sola revision vigente."""

import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.sire_control import SireControl, SireControlFile, SireControlReview, SireControlSource
from app.models.tax_period import TaxPeriod
from app.models.tenant import Tenant
from app.models.user import User
from app.services import sire_control_service as svc


@pytest.fixture()
def committed(test_engine):
    factory = sessionmaker(bind=test_engine, future=True)
    s = factory()
    tenant = Tenant(name="Tenant concurrencia M4")
    s.add(tenant)
    s.flush()
    user = User(
        tenant_id=tenant.id, email=f"m4-conc-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev", full_name="Contador", password_hash=hash_password("TestPassword123!"),
        role="contador", is_active=True,
    )
    client = Client(
        tenant_id=tenant.id, ruc="20" + str(uuid.uuid4().int)[:9], business_name="C", person_type="juridica", ruc_status="ACTIVO", fiscal_address_condition="HABIDO",
        tax_regime="GENERAL", service_status="activo",
    )
    s.add_all([user, client])
    s.flush()
    period = TaxPeriod(
        tenant_id=tenant.id, period_type="MONTHLY", fiscal_year=2026, month=1, canonical_code="2026-01", start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), is_open=True,
    )
    s.add(period)
    s.commit()
    ids = {"tenant": tenant.id, "user": user.id, "client": client.id, "period": period.id}
    s.close()
    yield factory, ids
    c = factory()
    for model in (SireControlReview, SireControlFile, SireControlSource, SireControl, AuditLog):
        c.execute(delete(model).where(model.tenant_id == ids["tenant"]))
    c.execute(delete(TaxPeriod).where(TaxPeriod.tenant_id == ids["tenant"]))
    c.execute(delete(Client).where(Client.tenant_id == ids["tenant"]))
    c.execute(delete(User).where(User.tenant_id == ids["tenant"]))
    c.execute(delete(Tenant).where(Tenant.id == ids["tenant"]))
    c.commit()
    c.close()


def _add(factory, ids, origin="SUNAT", total="10.00", replaces=None, key=None, gate=None):
    session = factory()
    try:
        if gate is not None:
            gate.wait(timeout=10)
        source, created, _control = svc.add_manual_source(
            session, tenant_id=ids["tenant"], actor_id=ids["user"], client_id=ids["client"], tax_period_id=ids["period"], component="RVIE", origin=origin, currency="PEN",
            record_count=1, base_amount=None, igv_amount=None, total_amount=total, source_label="fuente", source_date=date(2026, 1, 20), notes=None,
            replaces_source_id=replaces, idempotency_key=key,
        )
        session.commit()
        return ("ok", created, source.version)
    except svc.SireControlError as exc:
        session.rollback()
        return ("error", exc.code, None)
    finally:
        session.close()


def _race(fn, count=6):
    gate = threading.Event()
    results = [None] * count

    def run(i):
        results[i] = fn(i, gate)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    [t.start() for t in threads]
    gate.set()
    [t.join(timeout=60) for t in threads]
    return results


def test_concurrent_double_click_creates_a_single_control_and_a_single_version(committed):
    factory, ids = committed
    results = _race(lambda i, gate: _add(factory, ids, gate=gate))
    assert all(r[0] == "ok" for r in results), results
    assert sorted(r[1] for r in results).count(True) == 1  # exactamente UNA creacion; el resto son duplicados exactos
    s = factory()
    try:
        assert s.execute(select(func.count()).select_from(SireControl).where(SireControl.tenant_id == ids["tenant"])).scalar_one() == 1
        versions = s.execute(select(SireControlSource.version, SireControlSource.status).where(SireControlSource.tenant_id == ids["tenant"])).all()
        assert versions == [(1, "ACTIVE")]
    finally:
        s.close()


def test_concurrent_replacements_of_the_same_version_have_a_single_winner(committed):
    factory, ids = committed
    first = _add(factory, ids, total="10.00")
    s = factory()
    active = s.execute(select(SireControlSource.id).where(SireControlSource.tenant_id == ids["tenant"], SireControlSource.status == "ACTIVE")).scalar_one()
    s.close()
    results = _race(lambda i, gate: _add(factory, ids, total=f"{20 + i}.00", replaces=active, gate=gate))
    winners = [r for r in results if r[0] == "ok"]
    losers = [r for r in results if r[0] == "error"]
    assert first[0] == "ok" and len(winners) == 1 and winners[0][2] == 2
    assert {r[1] for r in losers} == {"SOURCE_CHANGED"} and len(losers) == 5
    s = factory()
    try:
        rows = s.execute(select(SireControlSource.version, SireControlSource.status).where(SireControlSource.tenant_id == ids["tenant"]).order_by(SireControlSource.version)).all()
        assert rows == [(1, "SUPERSEDED"), (2, "ACTIVE")]  # nada se pierde: el historial conserva la version previa
    finally:
        s.close()


def test_concurrent_reviews_keep_a_single_current_review_with_consecutive_versions(committed):
    factory, ids = committed
    _add(factory, ids, "SUNAT", "10.00")
    _add(factory, ids, "DARWIN", "10.00")
    s = factory()
    control = s.execute(select(SireControl).where(SireControl.tenant_id == ids["tenant"])).scalar_one()
    view = svc.control_view(s, tenant_id=ids["tenant"], client_id=ids["client"], period_id=ids["period"], component="RVIE", control=control)
    hash_ = view["comparison"]["comparison_hash"]
    control_id = control.id
    s.close()

    def review(i, gate):
        session = factory()
        try:
            gate.wait(timeout=10)
            svc.record_review(
                session, tenant_id=ids["tenant"], actor_id=ids["user"], control_id=control_id, decision="APPROVED", notes=None, comparison_hash=hash_,
                idempotency_key=uuid.uuid4(),
            )
            session.commit()
            return "ok"
        except svc.SireControlError as exc:
            session.rollback()
            return exc.code
        finally:
            session.close()

    results = _race(review)
    assert results.count("ok") == 6, results
    s = factory()
    try:
        rows = s.execute(select(SireControlReview.version, SireControlReview.status).where(SireControlReview.control_id == control_id).order_by(SireControlReview.version)).all()
        assert [v for v, _ in rows] == [1, 2, 3, 4, 5, 6] and [st for _, st in rows].count("CURRENT") == 1 and rows[-1][1] == "CURRENT"
    finally:
        s.close()


def test_idempotency_key_under_concurrency_creates_one_source(committed):
    factory, ids = committed
    key = uuid.uuid4()
    results = _race(lambda i, gate: _add(factory, ids, key=key, gate=gate))
    assert all(r[0] == "ok" for r in results) and sum(1 for r in results if r[1]) == 1
    s = factory()
    try:
        assert s.execute(select(func.count()).select_from(SireControlSource).where(SireControlSource.tenant_id == ids["tenant"])).scalar_one() == 1
    finally:
        s.close()
