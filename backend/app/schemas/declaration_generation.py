import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class GenerationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tax_period_id: uuid.UUID
    filters: dict | None = None


class DeclarationGenerationRunItemOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    client_obligation_id: uuid.UUID
    classification: str
    declaration_id: uuid.UUID | None
    error_detail: str | None
    excluded_by_user: bool
    created_at: datetime
    preview_schedule_rule_type: str | None
    preview_original_due_date: date | None
    preview_effective_due_date: date | None
    preview_resolution_hash: str | None

    model_config = {"from_attributes": True}


class DeclarationGenerationRunOut(BaseModel):
    id: uuid.UUID
    tax_period_id: uuid.UUID
    requested_by: uuid.UUID
    requested_at: datetime
    started_at: datetime | None
    filters: dict | None
    preview_checksum: str | None
    stale_diff: dict | None
    status: str
    total_analyzed: int
    total_created: int
    total_skipped: int
    total_failed: int
    errors: list | None
    correlation_id: uuid.UUID
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[DeclarationGenerationRunItemOut] = []

    model_config = {"from_attributes": True}


class GenerationItemExcludeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excluded_by_user: bool
