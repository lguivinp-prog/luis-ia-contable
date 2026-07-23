"""Periodos tributarios canonicos (ver ADR-0016): identidad por indices
unicos parciales por tipo + CHECK de canonicidad reforzado en base de datos
(insercion SQL directa), ademas de la validacion de servicio."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.tax_period import TaxPeriod
from app.services import tax_period_service
from app.services.tax_period_service import DuplicateTaxPeriodError, InvalidTaxPeriodError


def _insert_raw(db_session, **kwargs) -> None:
    period = TaxPeriod(**kwargs)
    db_session.add(period)
    db_session.flush()


def test_check_rejects_incoherent_monthly_canonical_code(db_session, tenant) -> None:
    with pytest.raises(IntegrityError):
        _insert_raw(
            db_session,
            tenant_id=tenant.id,
            period_type="MONTHLY",
            fiscal_year=2026,
            month=7,
            canonical_code="2026-08",
            schedule_tax_period_code="2026-07",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )


def test_check_rejects_incorrect_monthly_dates(db_session, tenant) -> None:
    with pytest.raises(IntegrityError):
        _insert_raw(
            db_session,
            tenant_id=tenant.id,
            period_type="MONTHLY",
            fiscal_year=2026,
            month=7,
            canonical_code="2026-07",
            schedule_tax_period_code="2026-07",
            start_date=date(2026, 7, 2),
            end_date=date(2026, 7, 31),
        )


def test_check_rejects_annual_with_month(db_session, tenant) -> None:
    with pytest.raises(IntegrityError):
        _insert_raw(
            db_session,
            tenant_id=tenant.id,
            period_type="ANNUAL",
            fiscal_year=2026,
            month=3,
            canonical_code="2026",
            schedule_tax_period_code="2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )


def test_check_rejects_special_with_wrong_year_schedule_code(db_session, tenant) -> None:
    with pytest.raises(IntegrityError):
        _insert_raw(
            db_session,
            tenant_id=tenant.id,
            period_type="SPECIAL",
            fiscal_year=2026,
            month=None,
            canonical_code="REGULARIZACION-2026",
            schedule_tax_period_code="2027-05",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )


def test_check_accepts_valid_period_of_each_type(db_session, tenant) -> None:
    _insert_raw(
        db_session,
        tenant_id=tenant.id,
        period_type="MONTHLY",
        fiscal_year=2026,
        month=7,
        canonical_code="2026-07",
        schedule_tax_period_code="2026-07",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
    )
    _insert_raw(
        db_session,
        tenant_id=tenant.id,
        period_type="ANNUAL",
        fiscal_year=2026,
        month=None,
        canonical_code="2026",
        schedule_tax_period_code="2026",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    _insert_raw(
        db_session,
        tenant_id=tenant.id,
        period_type="SPECIAL",
        fiscal_year=2026,
        month=None,
        canonical_code="REGULARIZACION-2026",
        schedule_tax_period_code=None,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )


def test_schedule_code_regex_rejects_month_00_and_13(db_session, tenant) -> None:
    for bad_code in ("2026-00", "2026-13"):
        savepoint = db_session.begin_nested()
        with pytest.raises(IntegrityError):
            _insert_raw(
                db_session,
                tenant_id=tenant.id,
                period_type="SPECIAL",
                fiscal_year=2026,
                month=None,
                canonical_code="X",
                schedule_tax_period_code=bad_code,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
        savepoint.rollback()


def test_two_special_periods_same_fiscal_year_allowed(db_session, tenant) -> None:
    tax_period_service.create_period(
        db_session,
        tenant.id,
        period_type="SPECIAL",
        fiscal_year=2026,
        month=None,
        canonical_code="REGULARIZACION-2026-A",
        schedule_tax_period_code=None,
        display_name=None,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        description=None,
    )
    tax_period_service.create_period(
        db_session,
        tenant.id,
        period_type="SPECIAL",
        fiscal_year=2026,
        month=None,
        canonical_code="REGULARIZACION-2026-B",
        schedule_tax_period_code=None,
        display_name=None,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 12, 31),
        description=None,
    )


def test_service_validation_rejects_bad_monthly_code_before_insert(db_session, tenant) -> None:
    with pytest.raises(InvalidTaxPeriodError):
        tax_period_service.build_period(
            period_type="MONTHLY",
            fiscal_year=2026,
            month=13,
            canonical_code=None,
            schedule_tax_period_code=None,
            display_name=None,
            start_date=None,
            end_date=None,
            description=None,
        )


def test_duplicate_monthly_period_rejected(db_session, tenant) -> None:
    tax_period_service.create_period(
        db_session,
        tenant.id,
        period_type="MONTHLY",
        fiscal_year=2026,
        month=5,
        canonical_code=None,
        schedule_tax_period_code=None,
        display_name=None,
        start_date=None,
        end_date=None,
        description=None,
    )
    with pytest.raises(DuplicateTaxPeriodError):
        tax_period_service.create_period(
            db_session,
            tenant.id,
            period_type="MONTHLY",
            fiscal_year=2026,
            month=5,
            canonical_code=None,
            schedule_tax_period_code=None,
            display_name=None,
            start_date=None,
            end_date=None,
            description=None,
        )


def test_ensure_period_open_blocks_closed_period(db_session, tenant) -> None:
    period = tax_period_service.create_period(
        db_session,
        tenant.id,
        period_type="MONTHLY",
        fiscal_year=2026,
        month=6,
        canonical_code=None,
        schedule_tax_period_code=None,
        display_name=None,
        start_date=None,
        end_date=None,
        description=None,
    )
    tax_period_service.close_period(db_session, tenant.id, None, period.id, "cierre de prueba")
    with pytest.raises(tax_period_service.TaxPeriodClosedError):
        tax_period_service.ensure_period_open(period)


def test_reopen_requires_reason(db_session, tenant) -> None:
    period = tax_period_service.create_period(
        db_session,
        tenant.id,
        period_type="MONTHLY",
        fiscal_year=2026,
        month=4,
        canonical_code=None,
        schedule_tax_period_code=None,
        display_name=None,
        start_date=None,
        end_date=None,
        description=None,
    )
    tax_period_service.close_period(db_session, tenant.id, None, period.id, None)
    with pytest.raises(tax_period_service.MissingTaxPeriodReopenReasonError):
        tax_period_service.reopen_period(db_session, tenant.id, None, period.id, "")
