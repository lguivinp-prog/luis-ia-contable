"""Acuerdos y cargos de honorarios profesionales (ver "Honorarios
profesionales" del plan / ADR-0044/0045). Ningun metodo de este
servicio ejecuta `commit()`/`rollback()`: usa `flush()` unicamente; el
router controla la transaccion (ver ADR-0037)."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.models.professional_fee import (
    ProfessionalFeeAgreement,
    ProfessionalFeeAgreementHistory,
    ProfessionalFeeCharge,
    ProfessionalFeeChargeStatusHistory,
)
from app.repositories import professional_fee_repository, tax_period_repository
from app.services import client_service
from app.services.professional_fee_charge_status import compute_charge_payment_status


class AgreementNotFoundError(Exception):
    pass


class AgreementOverlapError(Exception):
    pass


class AgreementNotActiveError(Exception):
    pass


class MissingLifecycleReasonError(Exception):
    pass


class InvalidValidUntilError(Exception):
    pass


class ChargeNotFoundError(Exception):
    pass


class ChargeAlreadyExistsError(Exception):
    pass


class TaxPeriodNotFoundError(Exception):
    pass


class InvalidChargeStatusTransitionError(Exception):
    pass


# --- acuerdos ---


def get_agreement(
    db: Session, tenant_id: uuid.UUID, agreement_id: uuid.UUID
) -> ProfessionalFeeAgreement:
    agreement = professional_fee_repository.get_agreement(db, tenant_id, agreement_id)
    if agreement is None:
        raise AgreementNotFoundError(f"Acuerdo {agreement_id} no encontrado")
    return agreement


def list_agreements(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ProfessionalFeeAgreement]:
    return professional_fee_repository.list_agreements_for_client(db, tenant_id, client_id)


def create_agreement(
    db: Session,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    service_type: str,
    amount: Decimal,
    billing_day: int | None,
    included_services: str | None,
    description: str | None,
    valid_from: date,
    valid_until: date | None,
    observations: str | None,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalFeeAgreement:
    client_service.get_client(db, tenant_id, client_id)

    agreement = ProfessionalFeeAgreement(
        tenant_id=tenant_id,
        client_id=client_id,
        service_type=service_type,
        amount=amount,
        billing_day=billing_day,
        included_services=included_services,
        description=description,
        status="ACTIVE",
        valid_from=valid_from,
        valid_until=valid_until,
        observations=observations,
    )
    try:
        professional_fee_repository.create_agreement(db, agreement)
    except IntegrityError as exc:
        raise AgreementOverlapError(
            "Ya existe un acuerdo vigente para este cliente y tipo de servicio en ese rango"
        ) from exc

    now = clock.utcnow()
    professional_fee_repository.create_agreement_history(
        db,
        ProfessionalFeeAgreementHistory(
            tenant_id=tenant_id,
            agreement_id=agreement.id,
            actor_user_id=actor_user_id,
            created_at=now,
            origin="MANUAL",
            audit_correlation_id=uuid.uuid4(),
            from_status=None,
            to_status="ACTIVE",
            from_valid_until=None,
            to_valid_until=valid_until,
        ),
    )
    db.flush()
    return agreement


def _close_agreement_validity(
    db: Session,
    tenant_id: uuid.UUID,
    agreement: ProfessionalFeeAgreement,
    *,
    new_status: str,
    effective_until: date | None,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalFeeAgreement:
    if not reason or not reason.strip():
        raise MissingLifecycleReasonError("La accion requiere un motivo explicito")
    if agreement.status != "ACTIVE":
        raise AgreementNotActiveError("El acuerdo ya no esta activo")

    resolved_until = effective_until or clock.utcnow().date()
    if resolved_until < agreement.valid_from:
        raise InvalidValidUntilError("La fecha de cierre no puede ser anterior a valid_from")

    from_status = agreement.status
    from_valid_until = agreement.valid_until

    agreement.status = new_status
    agreement.valid_until = resolved_until

    now = clock.utcnow()
    professional_fee_repository.create_agreement_history(
        db,
        ProfessionalFeeAgreementHistory(
            tenant_id=tenant_id,
            agreement_id=agreement.id,
            actor_user_id=actor_user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            audit_correlation_id=uuid.uuid4(),
            from_status=from_status,
            to_status=new_status,
            from_valid_until=from_valid_until,
            to_valid_until=resolved_until,
        ),
    )
    db.flush()
    return agreement


def supersede_agreement(
    db: Session,
    tenant_id: uuid.UUID,
    agreement: ProfessionalFeeAgreement,
    *,
    new_valid_until: date | None,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalFeeAgreement:
    """Cierra `valid_until` de verdad (nunca solo `status`, ver ADR-0044):
    esto es lo que realmente libera el rango de vigencia para que se pueda
    crear un acuerdo posterior no solapado -- el `EXCLUDE` compara
    `validity_range`, no `status`. Nunca modifica cargos ya generados."""
    return _close_agreement_validity(
        db,
        tenant_id,
        agreement,
        new_status="SUPERSEDED",
        effective_until=new_valid_until,
        reason=reason,
        actor_user_id=actor_user_id,
    )


def cancel_agreement(
    db: Session,
    tenant_id: uuid.UUID,
    agreement: ProfessionalFeeAgreement,
    *,
    effective_date: date | None,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalFeeAgreement:
    return _close_agreement_validity(
        db,
        tenant_id,
        agreement,
        new_status="CANCELLED",
        effective_until=effective_date,
        reason=reason,
        actor_user_id=actor_user_id,
    )


# --- cargos ---


def get_charge(db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID) -> ProfessionalFeeCharge:
    charge = professional_fee_repository.get_charge(db, tenant_id, charge_id)
    if charge is None:
        raise ChargeNotFoundError(f"Cargo {charge_id} no encontrado")
    return charge


def list_charges(
    db: Session, tenant_id: uuid.UUID, agreement_id: uuid.UUID
) -> list[ProfessionalFeeCharge]:
    return professional_fee_repository.list_charges_for_agreement(db, tenant_id, agreement_id)


def create_charge(
    db: Session,
    tenant_id: uuid.UUID,
    agreement: ProfessionalFeeAgreement,
    *,
    tax_period_id: uuid.UUID,
    amount: Decimal | None,
    due_date: date | None,
    observations: str | None,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalFeeCharge:
    if agreement.status != "ACTIVE":
        raise AgreementNotActiveError("Solo un acuerdo activo puede generar cargos nuevos")

    tax_period = tax_period_repository.get_period(db, tenant_id, tax_period_id)
    if tax_period is None:
        raise TaxPeriodNotFoundError(f"Periodo tributario {tax_period_id} no encontrado")

    charge = ProfessionalFeeCharge(
        tenant_id=tenant_id,
        agreement_id=agreement.id,
        tax_period_id=tax_period_id,
        amount=amount if amount is not None else agreement.amount,
        status="PENDING_ISSUANCE",
        due_date=due_date,
        observations=observations,
    )
    try:
        professional_fee_repository.create_charge(db, charge)
    except IntegrityError as exc:
        raise ChargeAlreadyExistsError(
            "Ya existe un cargo para este acuerdo y periodo tributario"
        ) from exc

    now = clock.utcnow()
    professional_fee_repository.create_charge_status_history(
        db,
        ProfessionalFeeChargeStatusHistory(
            tenant_id=tenant_id,
            charge_id=charge.id,
            actor_user_id=actor_user_id,
            created_at=now,
            origin="MANUAL",
            audit_correlation_id=uuid.uuid4(),
            from_status=None,
            to_status="PENDING_ISSUANCE",
        ),
    )
    db.flush()
    return charge


def issue_charge(
    db: Session,
    tenant_id: uuid.UUID,
    charge: ProfessionalFeeCharge,
    *,
    due_date: date | None,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalFeeCharge:
    """Emite el cargo (`PENDING_ISSUANCE -> ISSUED`). Si se define
    `due_date`, el cargo entra de inmediato al seguimiento de cobranza
    (recalcula a `PAYMENT_PENDING`, ya que aun no hay pagos) -- si no se
    define `due_date`, permanece en `ISSUED` sin cronograma de cobranza
    exigible (consistente con `compute_fee_collection_risk`: sin
    `due_date` el riesgo siempre es `ON_TRACK`)."""
    if charge.status != "PENDING_ISSUANCE":
        raise InvalidChargeStatusTransitionError(
            "Solo un cargo PENDING_ISSUANCE puede emitirse"
        )

    now = clock.utcnow()
    charge.issued_at = now
    charge.due_date = due_date

    from_status = charge.status
    if due_date is None:
        new_status = "ISSUED"
    else:
        new_status = compute_charge_payment_status(amount=charge.amount, total_paid=Decimal("0"))
    charge.status = new_status

    professional_fee_repository.create_charge_status_history(
        db,
        ProfessionalFeeChargeStatusHistory(
            tenant_id=tenant_id,
            charge_id=charge.id,
            actor_user_id=actor_user_id,
            created_at=now,
            origin="MANUAL",
            audit_correlation_id=uuid.uuid4(),
            from_status=from_status,
            to_status=new_status,
        ),
    )
    db.flush()
    return charge
