"""Determinacion tributaria y su historial (ver "Determinacion tributaria" /
"Correcciones e historial de determinaciones" del plan / ADR-0038/0039).
Ningun metodo de este servicio ejecuta `commit()`/`rollback()`: usa
`flush()` unicamente; el router controla la transaccion (ver ADR-0037). Debe
invocarse siempre con una `Declaration` ya bloqueada por
`SELECT ... FOR UPDATE` (ver `lock_declaration`/"Concurrencia financiera")."""

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import clock
from app.models.declaration import Declaration, DeclarationStatusHistory
from app.models.tax_assessment import TaxAssessment, TaxAssessmentHistory
from app.repositories import declaration_repository, tax_assessment_repository, tax_payment_repository
from app.services.tax_payment_status import compute_tax_payment_status


class DeclarationNotFoundError(Exception):
    pass


class AssessmentAlreadyExistsError(Exception):
    pass


class NoCurrentAssessmentError(Exception):
    pass


class MissingCorrectionReasonError(Exception):
    pass


def lock_declaration(db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID) -> Declaration:
    declaration = declaration_repository.lock_for_update(db, tenant_id, declaration_id)
    if declaration is None:
        raise DeclarationNotFoundError(f"Declaracion {declaration_id} no encontrada")
    return declaration


def get_current_assessment(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> TaxAssessment | None:
    return tax_assessment_repository.get_current(db, tenant_id, declaration_id)


def list_assessments(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[TaxAssessment]:
    return tax_assessment_repository.list_for_declaration(db, tenant_id, declaration_id)


def _snapshot(assessment: TaxAssessment) -> dict:
    return {
        "assessment_type": assessment.assessment_type,
        "sequence_number": assessment.sequence_number,
        "gross_tax_amount": str(assessment.gross_tax_amount),
        "credits_applied": str(assessment.credits_applied),
        "total_payable": str(assessment.total_payable),
        "remaining_credit": str(assessment.remaining_credit),
        "currency": assessment.currency,
        "assessed_at": assessment.assessed_at.isoformat(),
        "observations": assessment.observations,
    }


def _compute_amounts(gross_tax_amount: Decimal, credits_applied: Decimal) -> tuple[Decimal, Decimal]:
    total_payable = max(gross_tax_amount - credits_applied, Decimal("0"))
    remaining_credit = max(credits_applied - gross_tax_amount, Decimal("0"))
    return total_payable, remaining_credit


def _recalculate_tax_payment_status(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    *,
    total_payable: Decimal,
    remaining_credit: Decimal,
    actor_user_id: uuid.UUID | None,
    now,
    correlation_id: uuid.UUID,
    reason: str | None = None,
) -> None:
    total_paid = tax_payment_repository.sum_valid_amount_for_declaration(db, tenant_id, declaration.id)
    from_status = declaration.tax_payment_status
    new_status = compute_tax_payment_status(
        total_payable=total_payable, remaining_credit=remaining_credit, total_paid=total_paid
    )
    if new_status == from_status:
        return
    declaration.tax_payment_status = new_status
    declaration_repository.create_status_history_entry(
        db,
        DeclarationStatusHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=actor_user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            status_field="TAX_PAYMENT",
            from_value=from_status,
            to_value=new_status,
        ),
    )


def create_initial_assessment(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    *,
    gross_tax_amount: Decimal,
    credits_applied: Decimal,
    observations: str | None,
    actor_user_id: uuid.UUID | None,
) -> TaxAssessment:
    """Crea la determinacion inicial (`sequence_number=1`), su historial
    (`previous=None`), una fila de `declaration_status_history`
    (`NOT_DETERMINED -> derivado`) y recalcula `tax_payment_status` -- las 4
    escrituras en la misma transaccion del router (ver criterio de
    aceptacion 25)."""
    existing = tax_assessment_repository.get_current(db, tenant_id, declaration.id)
    if existing is not None:
        raise AssessmentAlreadyExistsError(
            "La declaracion ya tiene una determinacion vigente; use la correccion"
        )

    total_payable, remaining_credit = _compute_amounts(gross_tax_amount, credits_applied)
    now = clock.utcnow()
    correlation_id = uuid.uuid4()

    assessment = TaxAssessment(
        tenant_id=tenant_id,
        declaration_id=declaration.id,
        assessment_type="ORIGINAL",
        sequence_number=1,
        gross_tax_amount=gross_tax_amount,
        credits_applied=credits_applied,
        total_payable=total_payable,
        remaining_credit=remaining_credit,
        assessed_at=now,
        assessed_by=actor_user_id,
        observations=observations,
        is_current=True,
    )
    tax_assessment_repository.create(db, assessment)

    tax_assessment_repository.create_history(
        db,
        TaxAssessmentHistory(
            tenant_id=tenant_id,
            assessment_id=assessment.id,
            actor_user_id=actor_user_id,
            created_at=now,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            previous_assessment_snapshot=None,
            new_assessment_snapshot=_snapshot(assessment),
        ),
    )

    from_status = declaration.tax_payment_status
    new_status = compute_tax_payment_status(
        total_payable=total_payable, remaining_credit=remaining_credit, total_paid=Decimal("0")
    )
    declaration.tax_payment_status = new_status
    declaration_repository.create_status_history_entry(
        db,
        DeclarationStatusHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=actor_user_id,
            created_at=now,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            status_field="TAX_PAYMENT",
            from_value=from_status,
            to_value=new_status,
        ),
    )
    db.flush()
    return assessment


def correct_assessment(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    *,
    gross_tax_amount: Decimal,
    credits_applied: Decimal,
    observations: str | None,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> TaxAssessment:
    """Corrige la determinacion vigente: marca la anterior `is_current=false`,
    crea la nueva con `sequence_number+1`, y solo escribe una fila de
    `declaration_status_history` si el `tax_payment_status` derivado
    realmente cambio (ver criterio de aceptacion 25)."""
    if not reason or not reason.strip():
        raise MissingCorrectionReasonError("La correccion requiere un motivo explicito")

    current = tax_assessment_repository.get_current(db, tenant_id, declaration.id)
    if current is None:
        raise NoCurrentAssessmentError("No hay una determinacion vigente para corregir")

    previous_snapshot = _snapshot(current)
    current.is_current = False

    total_payable, remaining_credit = _compute_amounts(gross_tax_amount, credits_applied)
    now = clock.utcnow()
    correlation_id = uuid.uuid4()

    new_assessment = TaxAssessment(
        tenant_id=tenant_id,
        declaration_id=declaration.id,
        assessment_type="CORRECTION",
        sequence_number=current.sequence_number + 1,
        gross_tax_amount=gross_tax_amount,
        credits_applied=credits_applied,
        total_payable=total_payable,
        remaining_credit=remaining_credit,
        assessed_at=now,
        assessed_by=actor_user_id,
        observations=observations,
        is_current=True,
    )
    tax_assessment_repository.create(db, new_assessment)

    tax_assessment_repository.create_history(
        db,
        TaxAssessmentHistory(
            tenant_id=tenant_id,
            assessment_id=new_assessment.id,
            actor_user_id=actor_user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            previous_assessment_snapshot=previous_snapshot,
            new_assessment_snapshot=_snapshot(new_assessment),
        ),
    )

    _recalculate_tax_payment_status(
        db,
        tenant_id,
        declaration,
        total_payable=total_payable,
        remaining_credit=remaining_credit,
        actor_user_id=actor_user_id,
        now=now,
        correlation_id=correlation_id,
        reason=reason,
    )
    db.flush()
    return new_assessment
