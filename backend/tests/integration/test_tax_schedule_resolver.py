from datetime import date

import pytest

from app.models.tax_category import ClientTaxCategory, TaxCategoryCatalog
from app.models.tax_schedule import (
    TaxScheduleEntry,
    TaxScheduleException,
    TaxScheduleSource,
    TaxScheduleVersion,
)
from app.services.ruc_validator import compute_check_digit
from app.services.tax_schedule_resolver import (
    AmbiguousScheduleRuleError,
    NoApplicableScheduleRuleError,
    NoApprovedVersionError,
    resolve_due_date,
    resolve_reference_date,
    select_applicable_version,
)


def _fake_ruc(prefix: str = "20", base8: str = "12123434") -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def _make_client(auth_client, last_digit_ruc: str) -> dict:
    payload = {
        "ruc": last_digit_ruc,
        "business_name": "EMPRESA RESOLVER S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
    }
    return auth_client.post("/api/v1/clients", json=payload).json()


def _make_source(db_session, tenant_id, obligation_id, name="Cronograma resolver") -> TaxScheduleSource:
    source = TaxScheduleSource(tenant_id=tenant_id, obligation_id=obligation_id, name=name, is_active=True)
    db_session.add(source)
    db_session.flush()
    return source


def _make_version(
    db_session, tenant_id, source_id, *, fiscal_year=2026, scope_category_code=None, status="APPROVED"
) -> TaxScheduleVersion:
    version = TaxScheduleVersion(
        tenant_id=tenant_id,
        source_id=source_id,
        version_label=f"v-{fiscal_year}-{scope_category_code or 'general'}",
        fiscal_year=fiscal_year,
        schedule_type="GENERAL",
        scope_category_code=scope_category_code,
        status=status,
    )
    db_session.add(version)
    db_session.flush()
    return version


def _make_entry(
    db_session,
    tenant_id,
    version_id,
    *,
    tax_period="2026-01",
    digits=None,
    category_code=None,
    priority=None,
    due_effective=date(2026, 2, 12),
) -> TaxScheduleEntry:
    entry = TaxScheduleEntry(
        tenant_id=tenant_id,
        version_id=version_id,
        tax_period=tax_period,
        applicable_last_digits=digits,
        required_category_code=category_code,
        priority=priority,
        due_date_original=due_effective,
        due_date_effective=due_effective,
        due_date_type="REGULAR",
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _make_exception(
    db_session, tenant_id, version_id, *, tax_period="2026-01", digits=None, due_effective, reason
) -> TaxScheduleException:
    exception = TaxScheduleException(
        tenant_id=tenant_id,
        version_id=version_id,
        tax_period=tax_period,
        applicable_last_digits=digits,
        required_category_code=None,
        priority=None,
        due_date_effective=due_effective,
        reason=reason,
    )
    db_session.add(exception)
    db_session.flush()
    return exception


def _make_category_catalog(db_session, code: str) -> TaxCategoryCatalog:
    item = TaxCategoryCatalog(code=code, name=code.title(), is_active=True)
    db_session.add(item)
    db_session.flush()
    return item


def _assign_category(
    db_session, tenant_id, client_id, code, valid_from=date(2020, 1, 1)
) -> ClientTaxCategory:
    assignment = ClientTaxCategory(
        tenant_id=tenant_id, client_id=client_id, category_code=code, valid_from=valid_from, valid_until=None
    )
    db_session.add(assignment)
    db_session.flush()
    return assignment


def _resolve(db_session, tenant_id, source_id, client_id, last_digit, tax_period="2026-01"):
    return resolve_due_date(
        db_session,
        tenant_id,
        source_id=source_id,
        client_id=client_id,
        last_digit=last_digit,
        tax_period=tax_period,
    )


def test_resolve_reference_date_monthly() -> None:
    assert resolve_reference_date("2026-02") == date(2026, 2, 28)


def test_resolve_reference_date_annual() -> None:
    assert resolve_reference_date("2026") == date(2026, 12, 31)


def test_resolve_reference_date_rejects_unknown_format() -> None:
    with pytest.raises(ValueError):
        resolve_reference_date("26-01")


def test_resolver_picks_matching_general_entry(db_session, tenant, obligation_catalog, auth_client) -> None:
    client_data = _make_client(auth_client, _fake_ruc(base8="10000001"))
    last_digit = int(client_data["ruc"][10])
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    version = _make_version(db_session, tenant.id, source.id)
    _make_entry(db_session, tenant.id, version.id, digits=None, due_effective=date(2026, 2, 12))

    resolution = _resolve(db_session, tenant.id, source.id, client_data["id"], last_digit)
    assert resolution.due_date_effective == date(2026, 2, 12)
    assert resolution.rule_type == "entry"


def test_resolver_prefers_category_specific_entry_over_general(
    db_session, tenant, obligation_catalog, auth_client
) -> None:
    client_data = _make_client(auth_client, _fake_ruc(base8="10000002"))
    last_digit = int(client_data["ruc"][10])
    _make_category_catalog(db_session, "BUEN_CONTRIBUYENTE")
    _assign_category(db_session, tenant.id, client_data["id"], "BUEN_CONTRIBUYENTE")

    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    version = _make_version(db_session, tenant.id, source.id)
    _make_entry(
        db_session, tenant.id, version.id, digits=None, category_code=None, due_effective=date(2026, 2, 12)
    )
    _make_entry(
        db_session,
        tenant.id,
        version.id,
        digits=None,
        category_code="BUEN_CONTRIBUYENTE",
        due_effective=date(2026, 2, 20),
    )

    resolution = _resolve(db_session, tenant.id, source.id, client_data["id"], last_digit)
    assert resolution.due_date_effective == date(2026, 2, 20)
    assert resolution.matched_category_code == "BUEN_CONTRIBUYENTE"


def test_resolver_prefers_specific_digits_over_none(
    db_session, tenant, obligation_catalog, auth_client
) -> None:
    client_data = _make_client(auth_client, _fake_ruc(base8="10000003"))
    last_digit = int(client_data["ruc"][10])
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    version = _make_version(db_session, tenant.id, source.id)
    _make_entry(db_session, tenant.id, version.id, digits=None, due_effective=date(2026, 2, 10))
    _make_entry(db_session, tenant.id, version.id, digits=[last_digit], due_effective=date(2026, 2, 25))

    resolution = _resolve(db_session, tenant.id, source.id, client_data["id"], last_digit)
    assert resolution.due_date_effective == date(2026, 2, 25)


def test_resolver_exception_overrides_entry(db_session, tenant, obligation_catalog, auth_client) -> None:
    client_data = _make_client(auth_client, _fake_ruc(base8="10000004"))
    last_digit = int(client_data["ruc"][10])
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    version = _make_version(db_session, tenant.id, source.id)
    _make_entry(db_session, tenant.id, version.id, digits=None, due_effective=date(2026, 2, 12))
    _make_exception(
        db_session,
        tenant.id,
        version.id,
        digits=None,
        due_effective=date(2026, 3, 1),
        reason="Feriado nacional",
    )

    resolution = _resolve(db_session, tenant.id, source.id, client_data["id"], last_digit)
    assert resolution.rule_type == "exception"
    assert resolution.due_date_effective == date(2026, 3, 1)


def test_resolver_raises_ambiguous_on_tied_entries(
    db_session, tenant, obligation_catalog, auth_client
) -> None:
    client_data = _make_client(auth_client, _fake_ruc(base8="10000005"))
    last_digit = int(client_data["ruc"][10])
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    version = _make_version(db_session, tenant.id, source.id)
    _make_entry(db_session, tenant.id, version.id, digits=None, due_effective=date(2026, 2, 12))
    _make_entry(db_session, tenant.id, version.id, digits=None, due_effective=date(2026, 2, 20))

    with pytest.raises(AmbiguousScheduleRuleError):
        resolve_due_date(
            db_session,
            tenant.id,
            source_id=source.id,
            client_id=client_data["id"],
            last_digit=last_digit,
            tax_period="2026-01",
        )


def test_resolver_raises_no_applicable_rule_when_digit_not_covered(
    db_session, tenant, obligation_catalog, auth_client
) -> None:
    client_data = _make_client(auth_client, _fake_ruc(base8="10000006"))
    last_digit = int(client_data["ruc"][10])
    other_digit = (last_digit + 1) % 10
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    version = _make_version(db_session, tenant.id, source.id)
    _make_entry(db_session, tenant.id, version.id, digits=[other_digit], due_effective=date(2026, 2, 12))

    with pytest.raises(NoApplicableScheduleRuleError):
        resolve_due_date(
            db_session,
            tenant.id,
            source_id=source.id,
            client_id=client_data["id"],
            last_digit=last_digit,
            tax_period="2026-01",
        )


def test_resolver_raises_no_approved_version(db_session, tenant, obligation_catalog, auth_client) -> None:
    client_data = _make_client(auth_client, _fake_ruc(base8="10000007"))
    last_digit = int(client_data["ruc"][10])
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)

    with pytest.raises(NoApprovedVersionError):
        resolve_due_date(
            db_session,
            tenant.id,
            source_id=source.id,
            client_id=client_data["id"],
            last_digit=last_digit,
            tax_period="2026-01",
        )


def test_select_applicable_version_ambiguous_when_client_matches_two_category_scopes(
    db_session, tenant, obligation_catalog
) -> None:
    _make_category_catalog(db_session, "CAT_A")
    _make_category_catalog(db_session, "CAT_B")
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    _make_version(db_session, tenant.id, source.id, scope_category_code="CAT_A")
    _make_version(db_session, tenant.id, source.id, scope_category_code="CAT_B")

    with pytest.raises(AmbiguousScheduleRuleError):
        select_applicable_version(db_session, tenant.id, source.id, 2026, {"CAT_A", "CAT_B"})
