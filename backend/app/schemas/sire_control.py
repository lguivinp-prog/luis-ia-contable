"""M4 -- esquemas del control SIRE. Importes como CADENAS decimales (nunca float). Ninguna respuesta incluye rutas fisicas, claves de almacenamiento ni secretos."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Component = Literal["RVIE", "RCE"]
Origin = Literal["SUNAT", "DARWIN"]
Currency = Literal["PEN", "USD"]
Decision = Literal["APPROVED", "OBSERVED", "REQUIRES_REGULARIZATION"]
Amount = str | int | None  # los JSON float se rechazan: un importe llega como cadena decimal o entero


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualSourceRequest(_Strict):
    client_id: UUID
    tax_period_id: UUID
    component: Component
    origin: Origin
    currency: Currency
    record_count: int | None = Field(default=None, ge=0, le=2_000_000_000)
    base_amount: Amount = None
    igv_amount: Amount = None
    total_amount: Amount = None
    source_label: str = Field(min_length=1, max_length=120)
    source_date: date
    notes: str | None = Field(default=None, max_length=2000)
    replaces_source_id: UUID | None = None
    idempotency_key: UUID | None = None


class ImportMapping(_Strict):
    record_count: str | None = Field(default=None, max_length=200)
    base_amount: str | None = Field(default=None, max_length=200)
    igv_amount: str | None = Field(default=None, max_length=200)
    total_amount: str | None = Field(default=None, max_length=200)
    period: str | None = Field(default=None, max_length=200)
    record_type: str | None = Field(default=None, max_length=200)


class ImportRequest(_Strict):
    file_id: UUID
    mode: Literal["DETAIL", "SUMMARY"]
    decimal_style: Literal["AUTO", "DOT", "COMMA"] = "AUTO"
    mapping: ImportMapping
    ignore_total_rows: bool = False
    sheet: str | None = Field(default=None, max_length=120)
    encoding: Literal["utf-8-sig", "cp1252"] | None = None
    delimiter: Literal[",", ";", "\t", "|"] | None = None
    currency: Currency
    source_label: str = Field(min_length=1, max_length=120)
    source_date: date
    notes: str | None = Field(default=None, max_length=2000)
    replaces_source_id: UUID | None = None
    idempotency_key: UUID | None = None


class ReviewRequest(_Strict):
    decision: Decision
    notes: str | None = Field(default=None, max_length=2000)
    comparison_hash: str = Field(min_length=64, max_length=64)  # la comparacion que el contador VIO; si cambio, 409
    idempotency_key: UUID | None = None


class ToleranceRequest(_Strict):
    amount: Amount = None  # None = volver a la comparacion EXACTA
    reason: str | None = Field(default=None, max_length=500)
    expected_row_version: int = Field(ge=1)


# --- respuestas ---------------------------------------------------------------------------------------------------------------


class SourceOut(BaseModel):
    id: UUID
    origin: Origin
    entry_mode: Literal["MANUAL", "IMPORT"]
    version: int
    status: Literal["ACTIVE", "SUPERSEDED"]
    currency: Currency
    record_count: int | None
    base_amount: str | None
    igv_amount: str | None
    total_amount: str | None
    source_label: str
    source_date: date
    notes: str | None
    file_id: UUID | None
    import_summary: dict | None
    replaces_source_id: UUID | None
    created_by: UUID
    created_at: datetime
    superseded_at: datetime | None


class FileOut(BaseModel):
    id: UUID
    origin: Origin
    purpose: Literal["SOURCE_FILE", "EVIDENCE"]
    status: Literal["STAGED", "ACTIVE", "SUPERSEDED"]
    source_id: UUID | None
    original_filename: str
    description: str | None
    mime_type: str
    size_bytes: int
    sha256_hash: str
    uploaded_by: UUID
    uploaded_at: datetime


class ConceptOut(BaseModel):
    concept: Literal["record_count", "base_amount", "igv_amount", "total_amount"]
    sunat: str | None
    darwin: str | None
    difference: str | None  # DARWIN - SUNAT
    comparable: bool
    matches: bool | None
    exact: bool | None
    within_tolerance: bool


class ComparisonOut(BaseModel):
    status: Literal["NO_SOURCES", "PARTIAL", "MATCH", "DIFFERENCES", "NOT_COMPARABLE"]
    comparison_hash: str
    currency_mismatch: bool
    single_source: Literal["SUNAT", "DARWIN"] | None
    one_sided_concepts: list[str]
    tolerance: str | None
    tolerance_applied: bool
    messages: list[str]
    concepts: list[ConceptOut]


class ReviewOut(BaseModel):
    id: UUID
    version: int
    status: Literal["CURRENT", "SUPERSEDED"]
    decision: Decision
    notes: str | None
    comparison_status: str
    comparison_hash: str
    comparison_snapshot: dict
    sunat_source_id: UUID | None
    darwin_source_id: UUID | None
    reviewed_by: UUID
    reviewed_by_name: str | None = None
    reviewed_at: datetime
    superseded_at: datetime | None


class ControlOut(BaseModel):
    exists: bool
    id: UUID | None
    client_id: UUID
    tax_period_id: UUID
    component: Component
    summary_code: str  # NO_CONTROL | NO_SOURCES | PARTIAL | NOT_COMPARABLE | MATCH | DIFFERENCES | MATCH_REVIEWED | DIFFERENCES_REVIEWED | NEEDS_REVIEW
    row_version: int | None
    tolerance_amount: str | None
    tolerance_reason: str | None
    tolerance_set_at: datetime | None
    sunat_source: SourceOut | None
    darwin_source: SourceOut | None
    comparison: ComparisonOut
    current_review: ReviewOut | None
    review_stale: bool
    files: list[FileOut]
    history_counts: dict[str, int]
    created_at: datetime | None


class ControlsForPeriodOut(BaseModel):
    client_id: UUID
    tax_period_id: UUID
    tax_period_code: str
    controls: list[ControlOut]  # siempre RVIE y RCE (exists=false si aun no se creo el expediente; consultar NUNCA lo crea)
    disclaimer: str


class PreviewOut(BaseModel):
    control_id: UUID
    file: FileOut
    duplicate_of_existing: bool
    kind: Literal["csv", "xlsx"]
    sheets: list[str]
    sheet: str | None
    encoding: str | None
    delimiter: str | None
    headers: list[str]
    sample_rows: list[list[str]]
    truncated: bool
    suggested_mapping: dict[str, str | None]
    decimal_style_detected: str | None
    decimal_style_needs_choice: bool


class SourceCreatedOut(BaseModel):
    created: bool  # False = reintento idempotente / duplicado exacto de la fuente vigente
    source: SourceOut
    control: ControlOut


class FileStoredOut(BaseModel):
    created: bool
    file: FileOut


class HistoryItemOut(BaseModel):
    kind: Literal["SOURCE", "REVIEW", "FILE"]
    at: datetime
    version: int | None
    origin: str | None
    status: str
    label: str
    by: UUID | None
    ref_id: UUID


class HistoryOut(BaseModel):
    control_id: UUID
    items: list[HistoryItemOut]
    sources: list[SourceOut]
    reviews: list[ReviewOut]
    files: list[FileOut]


class FileIntegrityOut(BaseModel):
    file_id: UUID
    stored_sha256: str
    computed_sha256: str | None
    intact: bool
    present: bool


class ReportOut(BaseModel):
    report_version: int
    generated_at: datetime
    report_hash: str
    disclaimer: str
    identification: dict
    sources: dict
    comparison: dict
    differences: list[dict]
    files: list[dict]
    review: dict | None
    review_history: list[dict]
    status_legend: dict
