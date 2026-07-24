import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import ReasonStr


class DeclarationDocumentFileOut(BaseModel):
    """Nunca incluye `storage_key`, ruta fisica ni la raiz del volumen (ver
    ADR-0056) -- solo metadata opaca."""

    id: uuid.UUID
    logical_file_id: uuid.UUID
    version: int
    declaration_document_item_id: uuid.UUID
    original_filename: str
    description: str | None
    mime_type: str
    extension: str
    size_bytes: int
    sha256_hash: str
    status: str
    replaces_file_id: uuid.UUID | None
    uploaded_by: uuid.UUID
    uploaded_at: datetime
    superseded_at: datetime | None
    deleted_at: datetime | None
    deleted_by: uuid.UUID | None
    deletion_reason: str | None

    model_config = ConfigDict(from_attributes=True)


class DeclarationDocumentFileDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ReasonStr
