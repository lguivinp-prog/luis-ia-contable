import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentRequirementCatalogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    category: str | None = None


class DocumentRequirementCatalogOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    is_system: bool
    code: str
    name: str
    description: str | None
    category: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class ClientDocumentRequirementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_catalog_id: uuid.UUID
    client_obligation_id: uuid.UUID | None = None
    is_mandatory: bool = True
    periodicity: str = Field(min_length=1, max_length=20)
    valid_from: date
    valid_until: date | None = None
    description: str | None = None
    responsible_user_id: uuid.UUID | None = None
    reminder_lead_days: int = Field(default=5, ge=0)


class ClientDocumentRequirementOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    requirement_catalog_id: uuid.UUID
    client_obligation_id: uuid.UUID | None
    is_mandatory: bool
    periodicity: str
    valid_from: date
    valid_until: date | None
    description: str | None
    responsible_user_id: uuid.UUID | None
    reminder_lead_days: int
    is_active: bool

    model_config = {"from_attributes": True}


class DeclarationDocumentItemOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    declaration_id: uuid.UUID
    client_document_requirement_id: uuid.UUID
    status: str
    requested_at: datetime | None
    received_at: datetime | None
    reviewed_at: datetime | None
    observations: str | None
    responsible_user_id: uuid.UUID | None
    reminder_count: int
    last_reminder_at: datetime | None
    file_metadata: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentItemStatusChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    reason: str | None = None
    file_metadata: dict | None = None


class DocumentFileMetadataV1(BaseModel):
    """Metadatos de un documento -- nunca una ruta/URL real (ver "Control
    documental"). `extra='forbid'` rechaza explicitamente cualquier clave no
    declarada (`path`, `url`, `drive_id`, etc.)."""

    model_config = ConfigDict(extra="forbid")

    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    notes: str | None = None


class SyncRequirementsResult(BaseModel):
    created: list[uuid.UUID]
    existing: list[uuid.UUID]
    skipped: list[uuid.UUID]
    errors: list[str]
