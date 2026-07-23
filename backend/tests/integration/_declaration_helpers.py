"""Helpers compartidos para las pruebas de Bloque 3 (periodos, declaraciones,
generacion masiva). Construyen datos directamente via ORM (mas rapido que
pasar por HTTP) siguiendo el mismo patron que
tests/integration/test_tax_schedule_resolver.py."""

import uuid
from datetime import UTC, date, datetime

from app.core.security import hash_password
from app.models.client import Client
from app.models.obligation import ClientObligation
from app.models.tax_period import TaxPeriod
from app.models.tax_schedule import TaxScheduleEntry, TaxScheduleSource, TaxScheduleVersion
from app.models.user import User
from app.services.ruc_validator import compute_check_digit


def fake_ruc(prefix: str = "20", base8: str | None = None) -> str:
    base8 = base8 if base8 is not None else f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def make_client(db_session, tenant_id, *, ruc: str | None = None, name: str = "Cliente demo") -> Client:
    client = Client(
        tenant_id=tenant_id,
        ruc=ruc or fake_ruc(),
        business_name=name,
        person_type="juridica",
        ruc_status="ACTIVO",
        fiscal_address_condition="HABIDO",
        tax_regime="GENERAL",
        service_status="activo",
    )
    db_session.add(client)
    db_session.flush()
    return client


def make_client_obligation(
    db_session, tenant_id, client_id, obligation_id, *, is_active: bool = True
) -> ClientObligation:
    co = ClientObligation(
        tenant_id=tenant_id,
        client_id=client_id,
        obligation_id=obligation_id,
        is_active=is_active,
        assigned_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(co)
    db_session.flush()
    return co


def make_source(db_session, tenant_id, obligation_id, *, name: str = "Cronograma demo") -> TaxScheduleSource:
    source = TaxScheduleSource(tenant_id=tenant_id, obligation_id=obligation_id, name=name, is_active=True)
    db_session.add(source)
    db_session.flush()
    return source


def make_version(
    db_session, tenant_id, source_id, *, fiscal_year=2026, scope_category_code=None, status="APPROVED"
) -> TaxScheduleVersion:
    version = TaxScheduleVersion(
        tenant_id=tenant_id,
        source_id=source_id,
        version_label=f"v-{fiscal_year}-{scope_category_code or 'general'}-{uuid.uuid4().hex[:6]}",
        fiscal_year=fiscal_year,
        schedule_type="GENERAL",
        scope_category_code=scope_category_code,
        status=status,
    )
    db_session.add(version)
    db_session.flush()
    return version


def make_entry(
    db_session,
    tenant_id,
    version_id,
    *,
    tax_period="2026-01",
    digits=None,
    category_code=None,
    priority=None,
    due_original=date(2026, 2, 12),
    due_effective=date(2026, 2, 12),
) -> TaxScheduleEntry:
    entry = TaxScheduleEntry(
        tenant_id=tenant_id,
        version_id=version_id,
        tax_period=tax_period,
        applicable_last_digits=digits,
        required_category_code=category_code,
        priority=priority,
        due_date_original=due_original,
        due_date_effective=due_effective,
        due_date_type="REGULAR",
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def make_monthly_period(
    db_session, tenant_id, *, fiscal_year=2026, month=1, is_open=True
) -> TaxPeriod:
    from app.services import tax_period_service

    period = tax_period_service.build_period(
        period_type="MONTHLY",
        fiscal_year=fiscal_year,
        month=month,
        canonical_code=None,
        schedule_tax_period_code=None,
        display_name=None,
        start_date=None,
        end_date=None,
        description=None,
    )
    period.tenant_id = tenant_id
    period.is_open = is_open
    db_session.add(period)
    db_session.flush()
    return period


def make_other_tenant_with_user(db_session):
    from app.models.tenant import Tenant

    other_tenant = Tenant(name="Otro tenant")
    db_session.add(other_tenant)
    db_session.flush()
    other_user = User(
        tenant_id=other_tenant.id,
        email=f"other-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        full_name="Otro usuario",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.flush()
    return other_tenant, other_user
