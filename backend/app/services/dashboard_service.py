"""Agregado de honorarios para el tablero (ver "Tablero mensual completo --
sin duplicacion de filas" del plan): un cargo de honorarios no esta atado
1:1 a una declaracion -- se relaciona por `(client_id, tax_period_id)` via
el acuerdo, asi que el mismo agregado se repite (nunca se multiplica) en
cada declaracion de ese cliente/periodo. Se calcula con 2 consultas
agregadas (ver `professional_fee_repository`), nunca N+1 ni un JOIN que
multiplique filas de declaraciones."""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories import declaration_repository, professional_fee_repository
from app.services.professional_fee_risk import compute_fee_collection_risk

_RISK_PRIORITY = {"OVERDUE": 0, "DUE_TODAY": 1, "ON_TRACK": 2, "CANCELLED": 3, "PAID": 3}


@dataclass
class TaxAggregate:
    gross_tax_amount: Decimal | None
    total_payable: Decimal | None
    total_paid: Decimal
    missing_documents_count: int


_EMPTY_TAX_AGGREGATE = TaxAggregate(
    gross_tax_amount=None, total_payable=None, total_paid=Decimal("0"), missing_documents_count=0
)


def build_tax_aggregates(
    db: Session, tenant_id: uuid.UUID, declaration_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TaxAggregate]:
    unique_ids = list({d for d in declaration_ids})
    assessments = declaration_repository.list_current_assessments_for_declarations(
        db, tenant_id, unique_ids
    )
    paid = declaration_repository.sum_valid_payments_for_declarations(db, tenant_id, unique_ids)
    missing_documents = declaration_repository.count_missing_documents_for_declarations(
        db, tenant_id, unique_ids
    )

    result: dict[uuid.UUID, TaxAggregate] = {}
    for declaration_id in unique_ids:
        assessment = assessments.get(declaration_id)
        result[declaration_id] = TaxAggregate(
            gross_tax_amount=assessment.gross_tax_amount if assessment else None,
            total_payable=assessment.total_payable if assessment else None,
            total_paid=paid.get(declaration_id, Decimal("0")),
            missing_documents_count=missing_documents.get(declaration_id, 0),
        )
    return result


def get_tax_aggregate(
    aggregates: dict[uuid.UUID, TaxAggregate], declaration_id: uuid.UUID
) -> TaxAggregate:
    return aggregates.get(declaration_id, _EMPTY_TAX_AGGREGATE)


@dataclass
class FeeAggregate:
    total_charges: int
    total_amount: Decimal
    total_paid: Decimal
    balance: Decimal
    pending_charges_count: int
    worst_collection_risk: str | None


_EMPTY_AGGREGATE = FeeAggregate(
    total_charges=0,
    total_amount=Decimal("0"),
    total_paid=Decimal("0"),
    balance=Decimal("0"),
    pending_charges_count=0,
    worst_collection_risk=None,
)


def build_fee_aggregates(
    db: Session,
    tenant_id: uuid.UUID,
    pairs: list[tuple[uuid.UUID, uuid.UUID]],
    today: date,
) -> dict[tuple[uuid.UUID, uuid.UUID], FeeAggregate]:
    unique_pairs = list({pair for pair in pairs})
    charge_rows = professional_fee_repository.list_charges_for_client_period_pairs(
        db, tenant_id, unique_pairs
    )
    charge_ids = [charge.id for _, charge in charge_rows]
    paid_by_charge = professional_fee_repository.sum_valid_amounts_for_charges(db, tenant_id, charge_ids)

    grouped: dict[tuple[uuid.UUID, uuid.UUID], list] = {}
    for client_id, charge in charge_rows:
        grouped.setdefault((client_id, charge.tax_period_id), []).append(charge)

    result: dict[tuple[uuid.UUID, uuid.UUID], FeeAggregate] = {}
    for pair, charges in grouped.items():
        total_amount = sum((c.amount for c in charges), Decimal("0"))
        total_paid = sum((paid_by_charge.get(c.id, Decimal("0")) for c in charges), Decimal("0"))
        pending_count = sum(1 for c in charges if c.status not in ("PAID", "CANCELLED"))

        risks = [
            compute_fee_collection_risk(
                due_date=c.due_date,
                status=c.status,
                balance=c.amount - paid_by_charge.get(c.id, Decimal("0")),
                today=today,
            )
            for c in charges
        ]
        worst = min(risks, key=lambda r: _RISK_PRIORITY.get(r, 2)) if risks else None

        result[pair] = FeeAggregate(
            total_charges=len(charges),
            total_amount=total_amount,
            total_paid=total_paid,
            balance=total_amount - total_paid,
            pending_charges_count=pending_count,
            worst_collection_risk=worst,
        )
    return result


def get_fee_aggregate(
    aggregates: dict[tuple[uuid.UUID, uuid.UUID], FeeAggregate],
    client_id: uuid.UUID,
    tax_period_id: uuid.UUID,
) -> FeeAggregate:
    return aggregates.get((client_id, tax_period_id), _EMPTY_AGGREGATE)
