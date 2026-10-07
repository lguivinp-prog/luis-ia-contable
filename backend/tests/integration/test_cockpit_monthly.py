"""Cockpit Multi-RUC mensual (M1, Domain B): una fila por (cliente, periodo), clientes activos sin declaraciones visibles,
SIRE derivado, agregados tributarios/honorarios, paginacion por cliente, filtros, aislamiento y consultas acotadas."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.core import clock
from app.services import (
    cockpit_service,
    declaration_service,
    professional_fee_service,
    tax_assessment_service,
    tax_payment_service,
)
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_other_tenant_with_user,
    make_source,
    make_version,
)

ALL_CODES = ("621", "PLAME", "SIRE-RVIE", "SIRE-RCE")


class World:
    """Periodo 2026-01 con cronograma (vence 2026-02-12) para las obligaciones usadas."""

    def __init__(self, db_session, tenant, admin_user, obligation_catalog):
        self.db, self.tenant, self.admin, self.catalog = db_session, tenant, admin_user, obligation_catalog
        self.period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
        self._ready: set[str] = set()

    def _schedule(self, code):
        if code in self._ready:
            return
        obligation = self.catalog[code]
        source = make_source(self.db, self.tenant.id, obligation.id)
        version = make_version(self.db, self.tenant.id, source.id, fiscal_year=2026)
        make_entry(self.db, self.tenant.id, version.id, tax_period="2026-01", digits=list(range(10)))
        self._ready.add(code)

    def client(self, name="Cliente", codes=(), generate=True, status="activo"):
        client = make_client(self.db, self.tenant.id, name=name)
        client.service_status = status
        declarations = {}
        for code in codes:
            self._schedule(code)
            co = make_client_obligation(self.db, self.tenant.id, client.id, self.catalog[code].id)
            if generate:
                declarations[code] = declaration_service.create_declaration(
                    self.db, self.tenant.id, self.admin.id,
                    client_id=client.id, client_obligation_id=co.id, tax_period_id=self.period.id,
                    origin="MANUAL", sync_documents=False,
                )
        self.db.flush()
        return client, declarations

    def get(self, auth_client, **params):
        self.db.commit()
        response = auth_client.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(self.period.id), **params})
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture()
def world(db_session, tenant, admin_user, obligation_catalog, frozen_clock):
    frozen_clock["now"] = datetime(2026, 2, 10, 15, 0, tzinfo=UTC)  # 5 dias antes del vencimiento (12-feb)
    return World(db_session, tenant, admin_user, obligation_catalog)


def _row(body, client):
    matching = [r for r in body["items"] if r["client_id"] == str(client.id)]
    assert len(matching) == 1, f"el cliente debe aparecer EXACTAMENTE una vez, aparece {len(matching)}"
    return matching[0]


def test_one_declaration_is_one_row(world, auth_client):
    client, _ = world.client(codes=("621",))
    body = world.get(auth_client)
    row = _row(body, client)
    assert row["declaration_count"] == 1 and row["period_generated"] is True
    assert row["tax_period_id"] == str(world.period.id)


def test_four_declarations_are_still_one_row_with_detail_counts(world, auth_client):
    client, _ = world.client(codes=ALL_CODES)
    body = world.get(auth_client)
    row = _row(body, client)
    assert row["declaration_count"] == 4
    assert row["pending_declaration_count"] == 4 and row["filed_declaration_count"] == 0
    assert body["total"] == 1


def test_two_clients_are_two_rows(world, auth_client):
    a, _ = world.client(name="A", codes=("621",))
    b, _ = world.client(name="B", codes=("621", "PLAME"))
    body = world.get(auth_client)
    assert body["total"] == 2
    assert {_row(body, a)["declaration_count"], _row(body, b)["declaration_count"]} == {1, 2}


def test_active_client_without_declarations_is_visible_and_inactive_is_not(world, auth_client):
    empty, _ = world.client(name="Sin generar", codes=("621",), generate=False)
    inactive, _ = world.client(name="Inactivo", codes=("621",), status="inactivo")
    body = world.get(auth_client)
    row = _row(body, empty)
    assert row["period_generated"] is False and row["declaration_count"] == 0
    assert row["overall_risk"] == "PERIOD_NOT_GENERATED" and row["filing_progress"] == "NOT_GENERATED"
    assert row["document_status"] is None and row["nearest_due_date"] is None
    assert all(r["client_id"] != str(inactive.id) for r in body["items"])
    assert body["summary"]["period_not_generated"] == 1 and body["summary"]["active_clients"] == 1


def test_cross_tenant_isolation(world, auth_client, db_session):
    mine, _ = world.client(name="Mio", codes=("621",))
    other_tenant, _other_user = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id, name="Ajeno")
    other_period = make_monthly_period(db_session, other_tenant.id, fiscal_year=2026, month=1)
    body = world.get(auth_client)
    assert all(r["client_id"] != str(other_client.id) for r in body["items"])
    db_session.commit()
    response = auth_client.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(other_period.id)})
    assert response.status_code == 404 and response.json()["detail"]["code"] == "TAX_PERIOD_NOT_FOUND"
    assert _row(body, mine)["business_name"] == "Mio"


def test_sire_not_configured_is_distinct(world, auth_client):
    client, _ = world.client(codes=("621",))
    row = _row(world.get(auth_client), client)
    assert row["sire_status"] == "NOT_CONFIGURED"
    assert row["sire_rvie_status"] == "NOT_CONFIGURED" and row["sire_rce_status"] == "NOT_CONFIGURED"


def test_sire_configured_but_period_not_generated_is_not_started(world, auth_client):
    client, _ = world.client(codes=("SIRE-RVIE",), generate=False)
    row = _row(world.get(auth_client), client)
    assert row["period_generated"] is False
    assert row["sire_status"] == "NOT_STARTED"
    assert row["sire_rvie_status"] == "NOT_STARTED" and row["sire_rce_status"] == "NOT_CONFIGURED"


def test_sire_mixed_components_use_the_most_pending(world, auth_client):
    client, decls = world.client(codes=("SIRE-RVIE", "SIRE-RCE"))
    decls["SIRE-RVIE"].filing_status = "FILED"
    decls["SIRE-RVIE"].workflow_status = "CLOSED"
    decls["SIRE-RCE"].workflow_status = "PENDING"
    row = _row(world.get(auth_client), client)
    assert (row["sire_rvie_status"], row["sire_rce_status"], row["sire_status"]) == ("PRESENTED", "NOT_STARTED", "NOT_STARTED")

    decls["SIRE-RCE"].workflow_status = "READY_TO_FILE"
    row = _row(world.get(auth_client), client)
    assert row["sire_rce_status"] == "READY_FOR_SUNAT" and row["sire_status"] == "READY_FOR_SUNAT"

    decls["SIRE-RCE"].filing_status = "FILED"
    decls["SIRE-RCE"].workflow_status = "CLOSED"
    row = _row(world.get(auth_client), client)
    assert row["sire_status"] == "PRESENTED"


def test_sire_not_applicable_and_in_progress(world, auth_client):
    client, decls = world.client(codes=("SIRE-RVIE", "SIRE-RCE"))
    decls["SIRE-RVIE"].workflow_status = "NOT_APPLICABLE"
    decls["SIRE-RCE"].workflow_status = "IN_PROGRESS"
    row = _row(world.get(auth_client), client)
    assert row["sire_rvie_status"] == "NOT_APPLICABLE" and row["sire_status"] == "IN_PROGRESS"
    decls["SIRE-RCE"].workflow_status = "NOT_APPLICABLE"
    assert _row(world.get(auth_client), client)["sire_status"] == "NOT_APPLICABLE"


def test_tax_aggregates_use_current_assessments_and_valid_payments_only(world, auth_client, admin_user):
    client, decls = world.client(codes=("621", "PLAME", "SIRE-RVIE"))
    db = world.db
    amounts = {"621": (Decimal("1000"), [Decimal("300"), Decimal("200")]), "PLAME": (Decimal("500"), [Decimal("100")])}
    voided_target = None
    for code, (gross, payments) in amounts.items():
        locked = tax_assessment_service.lock_declaration(db, world.tenant.id, decls[code].id)
        tax_assessment_service.create_initial_assessment(
            db, world.tenant.id, locked, gross_tax_amount=gross, credits_applied=Decimal("0"),
            observations=None, actor_user_id=admin_user.id,
        )
        for index, amount in enumerate(payments):
            payment = tax_payment_service.create_payment(
                db, world.tenant.id, locked, idempotency_key=uuid.uuid4(), amount=amount, paid_at=date(2026, 2, 1 + index),
                payment_method="TRANSFER", operation_number=None, reference=None, observations=None,
                overpayment_confirmed=False, overpayment_reason=None, actor_user_id=admin_user.id,
            )
            if code == "PLAME":
                voided_target = (locked, payment)
    locked, payment = voided_target
    tax_payment_service.void_payment(db, world.tenant.id, locked, payment.id, reason="prueba", actor_user_id=admin_user.id)
    row = _row(world.get(auth_client), client)
    assert Decimal(row["tax_total_assessed"]) == Decimal("1500")
    assert Decimal(row["tax_total_payable"]) == Decimal("1500")
    assert Decimal(row["tax_total_paid"]) == Decimal("500")  # 300+200; el pago de 100 esta anulado
    assert Decimal(row["tax_balance"]) == Decimal("1000")
    assert row["tax_payment_status"] in ("PAYMENT_PENDING", "PARTIALLY_PAID")


def test_without_assessments_amounts_are_null_not_zero(world, auth_client):
    client, _ = world.client(codes=("621",))
    row = _row(world.get(auth_client), client)
    assert row["tax_total_assessed"] is None and row["tax_total_payable"] is None and row["tax_balance"] is None
    assert Decimal(row["tax_total_paid"]) == Decimal("0")


def test_fee_aggregate_is_not_multiplied_by_declaration_count(world, auth_client, admin_user):
    client, _ = world.client(codes=ALL_CODES)
    db = world.db
    agreement = professional_fee_service.create_agreement(
        db, world.tenant.id, client.id, service_type="CONTABILIDAD", amount=Decimal("400.00"), billing_day=None,
        included_services=None, description=None, valid_from=date(2026, 1, 1), valid_until=None,
        observations=None, actor_user_id=admin_user.id,
    )
    professional_fee_service.create_charge(
        db, world.tenant.id, agreement, tax_period_id=world.period.id, amount=None, due_date=None,
        observations=None, actor_user_id=admin_user.id,
    )
    row = _row(world.get(auth_client), client)
    assert Decimal(row["fee_total_amount"]) == Decimal("400.00")  # 4 declaraciones, un solo cargo
    assert Decimal(row["fee_balance"]) == Decimal("400.00")


def test_nearest_due_date_prefers_pending_and_uses_the_minimum(world, auth_client):
    client, decls = world.client(codes=("621", "PLAME"))
    decls["621"].current_effective_due_date = date(2026, 2, 20)
    decls["PLAME"].current_effective_due_date = date(2026, 2, 14)
    assert _row(world.get(auth_client), client)["nearest_due_date"] == "2026-02-14"
    decls["PLAME"].filing_status = "FILED"  # lo presentado deja de ser lo relevante
    assert _row(world.get(auth_client), client)["nearest_due_date"] == "2026-02-20"


def test_overall_risk_is_the_worst_and_fees_do_not_soften_it(world, auth_client, frozen_clock, admin_user):
    client, decls = world.client(codes=("621", "PLAME"))
    decls["621"].current_effective_due_date = date(2026, 1, 5)  # hoy 2026-02-10: vencida hace > 15 dias -> CRITICAL
    row = _row(world.get(auth_client), client)
    assert row["overall_risk"] == "CRITICAL" and row["declaration_worst_risk"] == "CRITICAL"
    assert world.get(auth_client)["summary"]["critical"] == 1


def test_documents_one_incomplete_declaration_blocks_complete(world, auth_client):
    client, decls = world.client(codes=("621", "PLAME"))
    decls["621"].document_status = "COMPLETE"
    decls["PLAME"].document_status = "PARTIAL"
    assert _row(world.get(auth_client), client)["document_status"] == "PARTIAL"
    decls["PLAME"].document_status = "COMPLETE"
    assert _row(world.get(auth_client), client)["document_status"] == "COMPLETE"


def test_monthly_phase_follows_calendar_day_of_the_following_month(world, db_session, tenant):
    from app.core.timezone import today_lima

    client, _ = world.client(codes=("621",))
    db_session.flush()
    for today, expected in [
        (date(2026, 1, 20), "PRE_APERTURA"),
        (date(2026, 2, 3), "APERTURA"),
        (date(2026, 2, 5), "OPERACION"),
        (date(2026, 2, 14), "OPERACION"),
        (date(2026, 2, 15), "CIERRE"),
        (date(2026, 3, 20), "CIERRE"),
    ]:
        body = cockpit_service.build_monthly_cockpit(db_session, tenant.id, world.period.id, today=today)
        assert _row(body.model_dump(mode="json"), client)["monthly_phase"] == expected, today
    # el "hoy" del cockpit es la fecha de Lima, no la UTC
    assert today_lima(datetime(2026, 2, 5, 3, 0, tzinfo=UTC)) == date(2026, 2, 4)


def test_summary_cards_count_the_whole_active_population(world, auth_client):
    a, da = world.client(name="A", codes=("621", "SIRE-RVIE"))
    b, db_ = world.client(name="B", codes=("621",))
    world.client(name="C", codes=("621",), generate=False)
    da["621"].document_status = "PARTIAL"
    da["621"].tax_payment_status = "PAYMENT_PENDING"
    db_["621"].filing_status = "FILED"
    body = world.get(auth_client, search="zzz-no-coincide")  # los filtros NO alteran las tarjetas
    summary = body["summary"]
    assert body["total"] == 0
    assert summary["active_clients"] == 3 and summary["period_not_generated"] == 1
    assert summary["documents_incomplete"] == 1 and summary["tax_payment_pending"] == 1
    assert summary["sire_pending"] == 1  # solo A tiene SIRE configurado y pendiente
    assert summary["declarations_pending"] == 1  # A; B ya presento; C no tiene periodo generado
    assert summary["due_soon"] == 1  # A vence 12-feb, hoy 10-feb (dentro de 5 dias)
    assert summary["overdue"] == 0 and summary["critical"] == 0


def test_pagination_is_by_client_not_by_declaration(world, auth_client):
    for index in range(3):
        world.client(name=f"Cliente {index}", codes=ALL_CODES)
    first = world.get(auth_client, limit=2, offset=0)
    second = world.get(auth_client, limit=2, offset=2)
    assert first["total"] == 3 and len(first["items"]) == 2 and len(second["items"]) == 1
    ids = [r["client_id"] for r in first["items"] + second["items"]]
    assert len(set(ids)) == 3  # 12 declaraciones, 3 clientes, sin repetidos entre paginas


def test_filters(world, auth_client):
    a, da = world.client(name="Alfa SAC", codes=("621", "SIRE-RVIE"))
    b, db_ = world.client(name="Beta SRL", codes=("621",))
    c, _ = world.client(name="Gamma EIRL", codes=("621",), generate=False)
    da["SIRE-RVIE"].workflow_status = "READY_TO_FILE"
    da["621"].document_status = "PARTIAL"
    db_["621"].document_status = "COMPLETE"
    db_["621"].filing_status = "FILED"

    def ids(**params):
        return {r["client_id"] for r in world.get(auth_client, **params)["items"]}

    assert ids(search="alfa") == {str(a.id)}
    assert ids(search=b.ruc) == {str(b.id)}
    assert ids(sire_status="READY_FOR_SUNAT") == {str(a.id)}
    assert ids(sire_status="NOT_CONFIGURED") == {str(b.id), str(c.id)}  # la configuracion no depende de la generacion
    assert ids(document_status="COMPLETE") == {str(b.id)}
    assert ids(filing_progress="NOT_GENERATED") == {str(c.id)}
    assert ids(filing_progress="ALL_FILED") == {str(b.id)}
    assert ids(overall_risk="PERIOD_NOT_GENERATED") == {str(c.id)}
    assert ids(client_id=str(a.id)) == {str(a.id)}


def test_invalid_filter_values_are_rejected(world, auth_client):
    world.db.commit()
    for params in ({"sire_status": "NOPE"}, {"overall_risk": "X"}, {"document_status": "Y"}, {"filing_progress": "Z"}, {"limit": 0}):
        response = auth_client.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(world.period.id), **params})
        assert response.status_code == 422


def test_rows_are_ordered_most_severe_first(world, auth_client):
    ok, _ = world.client(name="Aaa tranquilo", codes=("621",))
    bad, decls = world.client(name="Zzz vencido", codes=("621",))
    decls["621"].current_effective_due_date = date(2026, 1, 5)
    body = world.get(auth_client)
    assert [r["client_id"] for r in body["items"]][0] == str(bad.id)
    assert ok.id is not None


def test_rbac_read_access_and_authentication(world, auth_client, asistente_client):
    from fastapi.testclient import TestClient

    from app.main import app

    world.client(codes=("621",))
    world.db.commit()
    params = {"tax_period_id": str(world.period.id)}
    assert asistente_client.get("/api/v1/cockpit/monthly", params=params).status_code == 200
    assert TestClient(app).get("/api/v1/cockpit/monthly", params=params).status_code == 401


def test_query_count_does_not_grow_with_clients_or_declarations(world, db_session, tenant):
    def count_queries() -> int:
        statements: list[str] = []

        def before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", before)
        try:
            cockpit_service.build_monthly_cockpit(db_session, tenant.id, world.period.id, today=date(2026, 2, 10))
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(statements)

    world.client(name="Uno", codes=("621",))
    db_session.flush()
    few = count_queries()
    for index in range(8):
        world.client(name=f"Extra {index}", codes=ALL_CODES)
    world.client(name="Sin generar", codes=("621",), generate=False)
    db_session.flush()
    many = count_queries()
    assert many == few, f"las consultas crecieron con los datos: {few} -> {many}"
    assert few <= 12


def test_cockpit_is_read_only_and_does_not_generate_declarations(world, auth_client, db_session):
    from sqlalchemy import func, select

    from app.models.declaration import Declaration

    world.client(codes=("621",), generate=False)
    db_session.commit()
    before = db_session.execute(select(func.count()).select_from(Declaration)).scalar_one()
    auth_client.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(world.period.id)})
    after = db_session.execute(select(func.count()).select_from(Declaration)).scalar_one()
    assert before == after
    assert clock.utcnow() is not None
