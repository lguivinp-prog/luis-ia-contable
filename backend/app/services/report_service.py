"""Reportes CSV/Excel (ver "Reportes CSV y Excel" del plan / ADR-0049).
Un unico endpoint (`POST /api/v1/reports/export`) resuelve 10 tipos de
reporte contra consultas tenant-scoped, con un limite de filas configurable
(`settings.report_export_max_rows`). CSV se sanitiza con `sanitize_csv_cell`
(ver ADR de Bloque 2, correccion de CSV formula injection); Excel se genera
con `openpyxl` en modo `write_only` (una hoja "Datos" + una hoja
"Metadatos"), todo en memoria -- nunca un archivo temporal en disco."""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.declaration import Declaration
from app.models.declaration_document import DeclarationDocumentItem
from app.models.operational_task import OperationalTask
from app.models.professional_fee import (
    ProfessionalFeeAgreement,
    ProfessionalFeeCharge,
    ProfessionalFeePayment,
    ProfessionalReceipt,
)
from app.models.tax_assessment import TaxAssessment
from app.models.tax_payment import TaxPayment
from app.services.csv_utils import sanitize_csv_cell

settings = get_settings()

REPORT_TYPES = (
    "DECLARATIONS",
    "DECLARATION_DOCUMENTS",
    "TAX_ASSESSMENTS",
    "TAX_PAYMENTS",
    "FEE_AGREEMENTS",
    "FEE_CHARGES",
    "FEE_PAYMENTS",
    "FEE_RECEIPTS",
    "CLIENTS",
    "AUDIT_LOG",
    "TASKS",
)


class UnknownReportTypeError(Exception):
    pass


@dataclass
class ReportData:
    report_type: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def _build(tenant_id: uuid.UUID, report_type: str, *, tax_period_id, client_id) -> tuple[list[str], Select]:
    if report_type == "DECLARATIONS":
        columns = [
            "id", "client_id", "tax_period_id", "workflow_status", "filing_status",
            "tax_payment_status", "document_status", "current_effective_due_date",
            "closed_with_pending",
        ]
        query = select(
            Declaration.id, Declaration.client_id, Declaration.tax_period_id,
            Declaration.workflow_status, Declaration.filing_status,
            Declaration.tax_payment_status, Declaration.document_status,
            Declaration.current_effective_due_date, Declaration.closed_with_pending,
        ).where(Declaration.tenant_id == tenant_id)
        if tax_period_id is not None:
            query = query.where(Declaration.tax_period_id == tax_period_id)
        if client_id is not None:
            query = query.where(Declaration.client_id == client_id)
        return columns, query.order_by(Declaration.current_effective_due_date.asc())

    if report_type == "DECLARATION_DOCUMENTS":
        columns = [
            "id", "declaration_id", "client_id", "client_document_requirement_id",
            "status", "requested_at", "received_at", "reviewed_at",
        ]
        query = select(
            DeclarationDocumentItem.id, DeclarationDocumentItem.declaration_id,
            DeclarationDocumentItem.client_id, DeclarationDocumentItem.client_document_requirement_id,
            DeclarationDocumentItem.status, DeclarationDocumentItem.requested_at,
            DeclarationDocumentItem.received_at, DeclarationDocumentItem.reviewed_at,
        ).where(DeclarationDocumentItem.tenant_id == tenant_id)
        if client_id is not None:
            query = query.where(DeclarationDocumentItem.client_id == client_id)
        return columns, query.order_by(DeclarationDocumentItem.created_at.asc())

    if report_type == "TAX_ASSESSMENTS":
        columns = [
            "id", "declaration_id", "assessment_type", "sequence_number",
            "gross_tax_amount", "credits_applied", "total_payable", "remaining_credit",
            "assessed_at", "is_current",
        ]
        query = select(
            TaxAssessment.id, TaxAssessment.declaration_id, TaxAssessment.assessment_type,
            TaxAssessment.sequence_number, TaxAssessment.gross_tax_amount,
            TaxAssessment.credits_applied, TaxAssessment.total_payable,
            TaxAssessment.remaining_credit, TaxAssessment.assessed_at, TaxAssessment.is_current,
        ).where(TaxAssessment.tenant_id == tenant_id)
        return columns, query.order_by(TaxAssessment.assessed_at.asc())

    if report_type == "TAX_PAYMENTS":
        columns = [
            "id", "declaration_id", "assessment_id", "paid_at", "amount",
            "payment_method", "overpayment_confirmed", "is_voided",
        ]
        query = select(
            TaxPayment.id, TaxPayment.declaration_id, TaxPayment.assessment_id,
            TaxPayment.paid_at, TaxPayment.amount, TaxPayment.payment_method,
            TaxPayment.overpayment_confirmed, TaxPayment.is_voided,
        ).where(TaxPayment.tenant_id == tenant_id)
        return columns, query.order_by(TaxPayment.paid_at.asc())

    if report_type == "FEE_AGREEMENTS":
        columns = [
            "id", "client_id", "service_type", "amount", "status",
            "valid_from", "valid_until",
        ]
        query = select(
            ProfessionalFeeAgreement.id, ProfessionalFeeAgreement.client_id,
            ProfessionalFeeAgreement.service_type, ProfessionalFeeAgreement.amount,
            ProfessionalFeeAgreement.status, ProfessionalFeeAgreement.valid_from,
            ProfessionalFeeAgreement.valid_until,
        ).where(ProfessionalFeeAgreement.tenant_id == tenant_id)
        if client_id is not None:
            query = query.where(ProfessionalFeeAgreement.client_id == client_id)
        return columns, query.order_by(ProfessionalFeeAgreement.valid_from.asc())

    if report_type == "FEE_CHARGES":
        columns = ["id", "agreement_id", "tax_period_id", "amount", "status", "due_date"]
        query = select(
            ProfessionalFeeCharge.id, ProfessionalFeeCharge.agreement_id,
            ProfessionalFeeCharge.tax_period_id, ProfessionalFeeCharge.amount,
            ProfessionalFeeCharge.status, ProfessionalFeeCharge.due_date,
        ).where(ProfessionalFeeCharge.tenant_id == tenant_id)
        if tax_period_id is not None:
            query = query.where(ProfessionalFeeCharge.tax_period_id == tax_period_id)
        return columns, query.order_by(ProfessionalFeeCharge.created_at.asc())

    if report_type == "FEE_PAYMENTS":
        columns = [
            "id", "charge_id", "paid_at", "amount", "payment_method",
            "overpayment_confirmed", "is_voided",
        ]
        query = select(
            ProfessionalFeePayment.id, ProfessionalFeePayment.charge_id,
            ProfessionalFeePayment.paid_at, ProfessionalFeePayment.amount,
            ProfessionalFeePayment.payment_method, ProfessionalFeePayment.overpayment_confirmed,
            ProfessionalFeePayment.is_voided,
        ).where(ProfessionalFeePayment.tenant_id == tenant_id)
        return columns, query.order_by(ProfessionalFeePayment.created_at.asc())

    if report_type == "FEE_RECEIPTS":
        columns = [
            "id", "charge_id", "receipt_type", "number", "issued_at", "amount", "status",
        ]
        query = select(
            ProfessionalReceipt.id, ProfessionalReceipt.charge_id, ProfessionalReceipt.receipt_type,
            ProfessionalReceipt.number, ProfessionalReceipt.issued_at, ProfessionalReceipt.amount,
            ProfessionalReceipt.status,
        ).where(ProfessionalReceipt.tenant_id == tenant_id)
        return columns, query.order_by(ProfessionalReceipt.issued_at.asc())

    if report_type == "CLIENTS":
        columns = [
            "id", "ruc", "business_name", "person_type", "ruc_status", "service_status",
        ]
        query = select(
            Client.id, Client.ruc, Client.business_name, Client.person_type,
            Client.ruc_status, Client.service_status,
        ).where(Client.tenant_id == tenant_id)
        return columns, query.order_by(Client.business_name.asc())

    if report_type == "AUDIT_LOG":
        columns = ["id", "user_id", "action", "entity_type", "entity_id", "created_at"]
        query = select(
            AuditLog.id, AuditLog.user_id, AuditLog.action, AuditLog.entity_type,
            AuditLog.entity_id, AuditLog.created_at,
        ).where(AuditLog.tenant_id == tenant_id)
        return columns, query.order_by(AuditLog.created_at.desc())

    if report_type == "TASKS":
        columns = [
            "id", "title", "task_type", "priority", "status", "assigned_to",
            "due_at", "created_at", "completed_at", "client_id", "days_to_complete",
        ]
        days_to_complete = (OperationalTask.completed_at - OperationalTask.created_at).label(
            "days_to_complete"
        )
        query = select(
            OperationalTask.id, OperationalTask.title, OperationalTask.task_type,
            OperationalTask.priority, OperationalTask.status, OperationalTask.assigned_to,
            OperationalTask.due_at, OperationalTask.created_at, OperationalTask.completed_at,
            OperationalTask.client_id, days_to_complete,
        ).where(OperationalTask.tenant_id == tenant_id)
        if client_id is not None:
            query = query.where(OperationalTask.client_id == client_id)
        return columns, query.order_by(OperationalTask.due_at.asc())

    raise UnknownReportTypeError(f"Tipo de reporte desconocido: {report_type}")


def build_report_data(
    db: Session,
    tenant_id: uuid.UUID,
    report_type: str,
    *,
    tax_period_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
) -> ReportData:
    if report_type not in REPORT_TYPES:
        raise UnknownReportTypeError(f"Tipo de reporte desconocido: {report_type}")

    columns, query = _build(tenant_id, report_type, tax_period_id=tax_period_id, client_id=client_id)
    max_rows = settings.report_export_max_rows
    query = query.limit(max_rows + 1)
    raw_rows = db.execute(query).all()
    truncated = len(raw_rows) > max_rows
    rows = [list(row) for row in raw_rows[:max_rows]]
    return ReportData(report_type=report_type, columns=columns, rows=rows, truncated=truncated)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal | date | datetime | uuid.UUID):
        return str(value)
    return str(value)


def render_csv(data: ReportData) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(data.columns)
    for row in data.rows:
        writer.writerow([sanitize_csv_cell(_stringify(value)) for value in row])
    return buffer.getvalue()


def render_xlsx(data: ReportData, *, generated_at: datetime, generated_by: str) -> bytes:
    workbook = Workbook(write_only=True)

    data_sheet = workbook.create_sheet("Datos")
    data_sheet.append(data.columns)
    for row in data.rows:
        data_sheet.append([_stringify(value) for value in row])

    metadata_sheet = workbook.create_sheet("Metadatos")
    metadata_sheet.append(["Reporte", data.report_type])
    metadata_sheet.append(["Generado el", generated_at.isoformat()])
    metadata_sheet.append(["Generado por", generated_by])
    metadata_sheet.append(["Filas", len(data.rows)])
    metadata_sheet.append(["Truncado", "si" if data.truncated else "no"])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
