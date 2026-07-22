import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ObligationCatalogOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    periodicity: str
    applies_to_regime: list[str] | None
    source: str
    source_version: str
    is_active: bool

    model_config = {"from_attributes": True}


class ClientObligationCreate(BaseModel):
    obligation_id: uuid.UUID
    notes: str | None = None


class ClientObligationUpdate(BaseModel):
    is_active: bool | None = None
    notes: str | None = None


class ClientObligationOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    obligation_id: uuid.UUID
    is_active: bool
    assigned_at: datetime
    deactivated_at: datetime | None
    notes: str | None
    obligation: ObligationCatalogOut | None = Field(default=None)

    model_config = {"from_attributes": True}
