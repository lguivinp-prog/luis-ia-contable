"""Periodos tributarios canonicos (ver ADR-0016). La identidad real vive en
los indices unicos parciales por tipo del modelo/migracion; este servicio
deriva/valida canonical_code/schedule_tax_period_code/fechas ANTES de
intentar el INSERT, para dar mensajes de error legibles -- el CHECK de la
base de datos es la garantia de ultima linea, no el unico mecanismo."""

import calendar
import re
import uuid
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.models.tax_period import TaxPeriod, TaxPeriodStatusHistory
from app.repositories import tax_period_repository

_SCHEDULE_CODE_RE = re.compile(r"^[0-9]{4}(-(0[1-9]|1[0-2]))?$")


class TaxPeriodNotFoundError(Exception):
    pass


class DuplicateTaxPeriodError(Exception):
    pass


class InvalidTaxPeriodError(Exception):
    pass


class TaxPeriodClosedError(Exception):
    pass


class MissingTaxPeriodReopenReasonError(Exception):
    pass


def _validate_schedule_code(code: str | None, fiscal_year: int) -> None:
    if code is None:
        return
    if not _SCHEDULE_CODE_RE.match(code):
        raise InvalidTaxPeriodError(
            f"schedule_tax_period_code '{code}' no tiene un formato valido (YYYY o YYYY-MM, "
            "mes entre 01 y 12)"
        )
    if int(code[:4]) != fiscal_year:
        raise InvalidTaxPeriodError(
            f"schedule_tax_period_code '{code}' corresponde a un año distinto de fiscal_year "
            f"({fiscal_year})"
        )


def build_period(
    *,
    period_type: str,
    fiscal_year: int,
    month: int | None,
    canonical_code: str | None,
    schedule_tax_period_code: str | None,
    display_name: str | None,
    start_date: date | None,
    end_date: date | None,
    description: str | None,
) -> TaxPeriod:
    """Deriva/valida los campos canonicos segun el tipo de periodo. No toca
    la base de datos; la unicidad real la aplica el indice parcial del tipo
    correspondiente al intentar persistir."""
    if period_type == "MONTHLY":
        if month is None or not (1 <= month <= 12):
            raise InvalidTaxPeriodError("Un periodo MONTHLY requiere 'month' entre 1 y 12")
        derived_code = f"{fiscal_year}-{month:02d}"
        if canonical_code is not None and canonical_code != derived_code:
            raise InvalidTaxPeriodError(
                f"canonical_code debe ser '{derived_code}' para un periodo MONTHLY coherente"
            )
        last_day = calendar.monthrange(fiscal_year, month)[1]
        derived_start = date(fiscal_year, month, 1)
        derived_end = date(fiscal_year, month, last_day)
        if start_date is not None and start_date != derived_start:
            raise InvalidTaxPeriodError(f"start_date debe ser {derived_start.isoformat()}")
        if end_date is not None and end_date != derived_end:
            raise InvalidTaxPeriodError(f"end_date debe ser {derived_end.isoformat()}")
        _validate_schedule_code(schedule_tax_period_code or derived_code, fiscal_year)
        return TaxPeriod(
            period_type=period_type,
            fiscal_year=fiscal_year,
            month=month,
            canonical_code=derived_code,
            schedule_tax_period_code=schedule_tax_period_code or derived_code,
            display_name=display_name,
            start_date=derived_start,
            end_date=derived_end,
            description=description,
            is_open=True,
        )

    if period_type == "ANNUAL":
        if month is not None:
            raise InvalidTaxPeriodError("Un periodo ANNUAL no debe tener 'month'")
        derived_code = str(fiscal_year)
        if canonical_code is not None and canonical_code != derived_code:
            raise InvalidTaxPeriodError(
                f"canonical_code debe ser '{derived_code}' para un periodo ANNUAL coherente"
            )
        derived_start = date(fiscal_year, 1, 1)
        derived_end = date(fiscal_year, 12, 31)
        if start_date is not None and start_date != derived_start:
            raise InvalidTaxPeriodError(f"start_date debe ser {derived_start.isoformat()}")
        if end_date is not None and end_date != derived_end:
            raise InvalidTaxPeriodError(f"end_date debe ser {derived_end.isoformat()}")
        _validate_schedule_code(schedule_tax_period_code or derived_code, fiscal_year)
        return TaxPeriod(
            period_type=period_type,
            fiscal_year=fiscal_year,
            month=None,
            canonical_code=derived_code,
            schedule_tax_period_code=schedule_tax_period_code or derived_code,
            display_name=display_name,
            start_date=derived_start,
            end_date=derived_end,
            description=description,
            is_open=True,
        )

    if period_type == "SPECIAL":
        if not canonical_code or not canonical_code.strip():
            raise InvalidTaxPeriodError("Un periodo SPECIAL requiere 'canonical_code' explicito")
        if start_date is None or end_date is None:
            raise InvalidTaxPeriodError("Un periodo SPECIAL requiere 'start_date' y 'end_date'")
        if end_date < start_date:
            raise InvalidTaxPeriodError("end_date no puede ser anterior a start_date")
        _validate_schedule_code(schedule_tax_period_code, fiscal_year)
        return TaxPeriod(
            period_type=period_type,
            fiscal_year=fiscal_year,
            month=None,
            canonical_code=canonical_code,
            schedule_tax_period_code=schedule_tax_period_code,
            display_name=display_name,
            start_date=start_date,
            end_date=end_date,
            description=description,
            is_open=True,
        )

    raise InvalidTaxPeriodError(f"period_type '{period_type}' no reconocido")


def create_period(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    period_type: str,
    fiscal_year: int,
    month: int | None,
    canonical_code: str | None,
    schedule_tax_period_code: str | None,
    display_name: str | None,
    start_date: date | None,
    end_date: date | None,
    description: str | None,
) -> TaxPeriod:
    period = build_period(
        period_type=period_type,
        fiscal_year=fiscal_year,
        month=month,
        canonical_code=canonical_code,
        schedule_tax_period_code=schedule_tax_period_code,
        display_name=display_name,
        start_date=start_date,
        end_date=end_date,
        description=description,
    )
    period.tenant_id = tenant_id
    try:
        return tax_period_repository.create_period(db, period)
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateTaxPeriodError(
            "Ya existe un periodo con la misma identidad canonica para este tenant"
        ) from exc


def list_periods(db: Session, tenant_id: uuid.UUID) -> list[TaxPeriod]:
    return tax_period_repository.list_periods(db, tenant_id)


def get_period(db: Session, tenant_id: uuid.UUID, period_id: uuid.UUID) -> TaxPeriod:
    period = tax_period_repository.get_period(db, tenant_id, period_id)
    if period is None:
        raise TaxPeriodNotFoundError(f"Periodo {period_id} no encontrado")
    return period


def ensure_period_open(period: TaxPeriod) -> None:
    if not period.is_open:
        raise TaxPeriodClosedError(
            f"El periodo '{period.canonical_code}' esta cerrado; no admite nuevas operaciones"
        )


def close_period(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    period_id: uuid.UUID,
    reason: str | None,
) -> TaxPeriod:
    period = get_period(db, tenant_id, period_id)
    from_is_open = period.is_open
    period.is_open = False
    db.flush()
    tax_period_repository.create_status_history_entry(
        db,
        TaxPeriodStatusHistory(
            tenant_id=tenant_id,
            tax_period_id=period.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=reason,
            origin="MANUAL",
            from_is_open=from_is_open,
            to_is_open=False,
        ),
    )
    return period


def reopen_period(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID | None, period_id: uuid.UUID, reason: str
) -> TaxPeriod:
    if not reason or not reason.strip():
        raise MissingTaxPeriodReopenReasonError("La reapertura de un periodo requiere un motivo")
    period = get_period(db, tenant_id, period_id)
    from_is_open = period.is_open
    period.is_open = True
    db.flush()
    tax_period_repository.create_status_history_entry(
        db,
        TaxPeriodStatusHistory(
            tenant_id=tenant_id,
            tax_period_id=period.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=reason,
            origin="MANUAL",
            from_is_open=from_is_open,
            to_is_open=True,
        ),
    )
    return period


def list_status_history(
    db: Session, tenant_id: uuid.UUID, period_id: uuid.UUID
) -> list[TaxPeriodStatusHistory]:
    return tax_period_repository.list_status_history(db, tenant_id, period_id)
