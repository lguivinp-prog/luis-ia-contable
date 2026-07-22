import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TaxScheduleSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obligation_id: uuid.UUID
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    reference_url: str | None = None


class TaxScheduleSourceOut(BaseModel):
    id: uuid.UUID
    obligation_id: uuid.UUID
    name: str
    description: str | None
    reference_url: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class TaxScheduleVersionOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    version_label: str
    fiscal_year: int
    schedule_type: str
    scope_category_code: str | None
    status: str
    normative_reference: str | None
    source_document_url: str | None
    checksum: str | None
    superseded_by_version_id: uuid.UUID | None
    observations: str | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TaxScheduleEntryOut(BaseModel):
    id: uuid.UUID
    tax_period: str
    applicable_last_digits: list[int] | None
    required_category_code: str | None
    priority: int | None
    due_date_original: date
    due_date_effective: date
    due_date_type: str
    notes: str | None

    model_config = {"from_attributes": True}


class SubmitForReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = None


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class ApprovalHistoryOut(BaseModel):
    id: uuid.UUID
    from_status: str | None
    to_status: str
    actor_user_id: uuid.UUID | None
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CompareVersionsOut(BaseModel):
    version_a: str
    version_b: str
    added: list[dict]
    removed: list[dict]
    modified: list[dict]


class ImportRunOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    version_id: uuid.UUID | None
    original_filename: str
    file_checksum: str
    fiscal_year: int
    schedule_type: str
    scope_category_code: str | None
    row_count: int | None
    status: str
    validation_errors: list | None
    diff_summary: dict | None
    parsed_rows: list | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConfirmImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_label: str = Field(min_length=1, max_length=50)
    normative_reference: str | None = None
    source_document_url: str | None = None


class PreviewDueDateOut(BaseModel):
    version_id: uuid.UUID
    version_label: str
    source_name: str
    rule_type: str
    last_digit: int
    matched_category_code: str | None
    due_date_original: date | None
    due_date_effective: date
    due_date_type: str
    is_extension: bool
    warnings: list[str]
    demo_disclaimer: str = (
        "DATOS DE DEMOSTRACIÓN: esta fecha proviene de un cronograma ficticio. "
        "No es válida para cumplimiento tributario real."
    )
