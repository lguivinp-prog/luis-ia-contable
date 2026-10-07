"""Cockpit Multi-RUC mensual (M1, Domain B): vista de SOLO LECTURA, una fila por (cliente, periodo).

Consultas a base de datos (cantidad FIJA, sin N+1, independiente del numero de clientes/declaraciones):
  1. periodo; 2. clientes activos; 3. declaraciones del periodo (+ codigo de obligacion); 4. obligaciones SIRE configuradas;
  5-7. liquidaciones vigentes / pagos validos / documentos faltantes (agregados existentes); 8-9. honorarios (agregado
  existente por (cliente, periodo), nunca multiplicado por el numero de declaraciones).
Las filas de TODA la poblacion activa se derivan en Python; los filtros y la paginacion operan por CLIENTE sobre ese
resultado (escala esperada: decenas/pocos cientos de clientes). Nada se persiste y no se genera ninguna declaracion."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.declaration import Declaration
from app.models.obligation import ClientObligation, ObligationCatalog
from app.models.tax_period import TaxPeriod
from app.schemas.cockpit import CockpitOut, CockpitPeriodOut, CockpitRowOut, CockpitSummaryOut
from app.services import cockpit_logic as logic
from app.services import dashboard_service
from app.services.declaration_risk import UPCOMING_THRESHOLD_DAYS, compute_risk_status

ACTIVE_SERVICE_STATUS = "activo"
_NOT_COUNTED_WORKFLOW = ("CANCELLED", "NOT_APPLICABLE")
_FILED_FILING = ("FILED", "NOT_REQUIRED")
_SIRE_PENDING_STATES = ("NOT_STARTED", "IN_PROGRESS", "READY_FOR_SUNAT")
_OVERDUE_RISKS = ("OVERDUE", "CRITICAL")


class CockpitPeriodNotFoundError(Exception):
    pass


def _build_rows(db: Session, tenant_id: uuid.UUID, period: TaxPeriod, today: date) -> list[CockpitRowOut]:
    clients = list(
        db.execute(
            select(Client)
            .where(Client.tenant_id == tenant_id, Client.service_status == ACTIVE_SERVICE_STATUS)
            .order_by(Client.business_name.asc())
        )
        .scalars()
        .all()
    )
    if not clients:
        return []
    client_ids = [c.id for c in clients]

    declaration_rows = db.execute(
        select(Declaration, ObligationCatalog.code)
        .join(ClientObligation, ClientObligation.id == Declaration.client_obligation_id)
        .join(ObligationCatalog, ObligationCatalog.id == ClientObligation.obligation_id)
        .where(
            Declaration.tenant_id == tenant_id,
            Declaration.tax_period_id == period.id,
            Declaration.client_id.in_(client_ids),
        )
    ).all()
    declarations_by_client: dict[uuid.UUID, list[tuple[Declaration, str]]] = {}
    for declaration, code in declaration_rows:
        declarations_by_client.setdefault(declaration.client_id, []).append((declaration, code))

    configured_sire: dict[uuid.UUID, set[str]] = {}
    for client_id, code in db.execute(
        select(ClientObligation.client_id, ObligationCatalog.code)
        .join(ObligationCatalog, ObligationCatalog.id == ClientObligation.obligation_id)
        .where(
            ClientObligation.tenant_id == tenant_id,
            ClientObligation.client_id.in_(client_ids),
            ClientObligation.is_active.is_(True),
            ObligationCatalog.code.in_(logic.SIRE_CODES),
        )
    ).all():
        configured_sire.setdefault(client_id, set()).add(code)

    applicable_ids = [
        d.id
        for entries in declarations_by_client.values()
        for d, _ in entries
        if d.workflow_status not in _NOT_COUNTED_WORKFLOW
    ]
    tax_aggregates = dashboard_service.build_tax_aggregates(db, tenant_id, applicable_ids)
    fee_aggregates = dashboard_service.build_fee_aggregates(db, tenant_id, [(cid, period.id) for cid in client_ids], today)
    phase = logic.monthly_phase(period.end_date, today)

    rows: list[CockpitRowOut] = []
    for client in clients:
        entries = declarations_by_client.get(client.id, [])
        period_generated = len(entries) > 0
        applicable = [(d, code) for d, code in entries if d.workflow_status not in _NOT_COUNTED_WORKFLOW]
        pending = [(d, code) for d, code in applicable if d.filing_status not in _FILED_FILING]
        filed_count = sum(1 for d, _ in applicable if d.filing_status == "FILED")

        due_pool = pending or applicable
        nearest_due = min((d.current_effective_due_date for d, _ in due_pool), default=None)

        risks = [
            compute_risk_status(
                d.current_effective_due_date,
                d.workflow_status,
                d.filing_status,
                today,
                document_status=d.document_status,
                tax_payment_status=d.tax_payment_status,
                closed_with_pending=d.closed_with_pending,
            )
            for d, _ in applicable
        ]
        document_status = logic.aggregate_document_status([d.document_status for d, _ in applicable])
        tax_payment_status = logic.aggregate_tax_payment_status([d.tax_payment_status for d, _ in applicable])

        assessed = payable = None
        paid = Decimal("0")
        missing_documents = 0
        for d, _ in applicable:
            agg = dashboard_service.get_tax_aggregate(tax_aggregates, d.id)
            if agg.gross_tax_amount is not None:
                assessed = (assessed or Decimal("0")) + agg.gross_tax_amount
            if agg.total_payable is not None:
                payable = (payable or Decimal("0")) + agg.total_payable
            paid += agg.total_paid
            missing_documents += agg.missing_documents_count
        balance = payable - paid if payable is not None else None

        fee = dashboard_service.get_fee_aggregate(fee_aggregates, client.id, period.id)

        # SIRE: configurado = obligacion activa asignada O declaracion SIRE existente en el periodo.
        by_code = {code: d for d, code in entries if code in logic.SIRE_CODES}
        configured = configured_sire.get(client.id, set()) | set(by_code)
        component: dict[str, str | None] = {}
        for code in logic.SIRE_CODES:
            if code not in configured:
                component[code] = "NOT_CONFIGURED"
            elif not period_generated:
                component[code] = None  # configurado, pero el periodo aun no se genero: no derivable
            elif code in by_code and by_code[code].workflow_status != "CANCELLED":
                declaration = by_code[code]
                component[code] = logic.sire_component_status(declaration.workflow_status, declaration.filing_status)
            else:
                component[code] = "NOT_STARTED"  # configurado y periodo generado, sin declaracion vigente
        if not configured:
            sire_status: str | None = "NOT_CONFIGURED"
        elif not period_generated:
            sire_status = None
        else:
            sire_status = logic.combine_sire([s for s in component.values() if s not in (None, "NOT_CONFIGURED")])

        rows.append(
            CockpitRowOut(
                client_id=client.id,
                business_name=client.business_name,
                ruc=client.ruc,
                ruc_last_digit=client.ruc_last_digit,
                service_status=client.service_status,
                tax_period_id=period.id,
                tax_period_code=period.canonical_code,
                tax_period_display=period.display_name,
                monthly_phase=phase,
                period_generated=period_generated,
                nearest_due_date=nearest_due,
                declaration_count=len(applicable),
                filed_declaration_count=filed_count,
                pending_declaration_count=len(pending),
                filing_progress=logic.filing_progress(
                    period_generated=period_generated,
                    applicable=len(applicable),
                    pending=len(pending),
                    filed=filed_count,
                ),
                document_status=document_status,
                missing_documents_count=missing_documents,
                sire_status=sire_status,
                sire_rvie_status=component["SIRE-RVIE"],
                sire_rce_status=component["SIRE-RCE"],
                tax_total_assessed=assessed,
                tax_total_payable=payable,
                tax_total_paid=paid,
                tax_balance=balance,
                tax_payment_status=tax_payment_status,
                fee_total_amount=fee.total_amount,
                fee_total_paid=fee.total_paid,
                fee_balance=fee.balance,
                fee_worst_collection_risk=fee.worst_collection_risk,
                overall_risk=logic.overall_risk(
                    risks,
                    period_generated=period_generated,
                    document_status=document_status,
                    tax_payment_status=tax_payment_status,
                    fee_collection_risk=fee.worst_collection_risk,
                ),
                declaration_worst_risk=min(
                    (r for r in risks if r not in ("CANCELLED", "NOT_APPLICABLE")), key=logic.risk_rank, default=None
                ),
            )
        )
    return rows


def _summarize(rows: list[CockpitRowOut], today: date) -> CockpitSummaryOut:
    soon_limit = today + timedelta(days=UPCOMING_THRESHOLD_DAYS)
    return CockpitSummaryOut(
        active_clients=len(rows),
        period_not_generated=sum(1 for r in rows if not r.period_generated),
        due_soon=sum(
            1
            for r in rows
            if r.pending_declaration_count > 0 and r.nearest_due_date is not None and today <= r.nearest_due_date <= soon_limit
        ),
        overdue=sum(1 for r in rows if r.declaration_worst_risk in _OVERDUE_RISKS),
        documents_incomplete=sum(1 for r in rows if r.document_status in logic.DOCUMENT_INCOMPLETE),
        sire_pending=sum(1 for r in rows if r.sire_status in _SIRE_PENDING_STATES),
        declarations_pending=sum(1 for r in rows if r.pending_declaration_count > 0),
        tax_payment_pending=sum(1 for r in rows if r.tax_payment_status in logic.TAX_PAYMENT_PENDING),
        critical=sum(1 for r in rows if r.overall_risk == "CRITICAL"),
    )


def build_monthly_cockpit(
    db: Session,
    tenant_id: uuid.UUID,
    tax_period_id: uuid.UUID,
    *,
    today: date,
    search: str | None = None,
    overall_risk: str | None = None,
    sire_status: str | None = None,
    document_status: str | None = None,
    filing_progress: str | None = None,
    client_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CockpitOut:
    period = db.execute(
        select(TaxPeriod).where(TaxPeriod.id == tax_period_id, TaxPeriod.tenant_id == tenant_id)
    ).scalar_one_or_none()
    if period is None:
        raise CockpitPeriodNotFoundError()

    rows = _build_rows(db, tenant_id, period, today)
    summary = _summarize(rows, today)

    needle = search.strip().lower() if search else ""
    filtered = [
        r
        for r in rows
        if (not needle or needle in r.business_name.lower() or needle in r.ruc)
        and (overall_risk is None or r.overall_risk == overall_risk)
        and (sire_status is None or r.sire_status == sire_status)
        and (document_status is None or r.document_status == document_status)
        and (filing_progress is None or r.filing_progress == filing_progress)
        and (client_id is None or r.client_id == client_id)
    ]
    filtered.sort(
        key=lambda r: (logic.risk_rank(r.overall_risk), r.nearest_due_date or date.max, r.business_name.lower())
    )
    page = filtered[offset : offset + limit]
    return CockpitOut(
        period=CockpitPeriodOut(
            id=period.id,
            period_type=period.period_type,
            canonical_code=period.canonical_code,
            display_name=period.display_name,
            start_date=period.start_date,
            end_date=period.end_date,
            is_open=period.is_open,
        ),
        today=today,
        summary=summary,
        items=page,
        total=len(filtered),
        limit=limit,
        offset=offset,
    )
