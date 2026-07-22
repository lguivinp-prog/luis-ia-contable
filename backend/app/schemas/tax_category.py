import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TaxCategoryCatalogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None


class TaxCategoryCatalogOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class ClientTaxCategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_code: str
    valid_from: date
    valid_until: date | None = None
    notes: str | None = None


class ClientTaxCategoryOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    category_code: str
    valid_from: date
    valid_until: date | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
