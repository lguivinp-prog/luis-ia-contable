import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TaxPeriodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_type: str = Field(pattern="^(MONTHLY|ANNUAL|SPECIAL)$")
    fiscal_year: int = Field(ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    canonical_code: str | None = Field(default=None, max_length=50)
    schedule_tax_period_code: str | None = Field(default=None, max_length=7)
    display_name: str | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = None


class TaxPeriodOut(BaseModel):
    id: uuid.UUID
    period_type: str
    fiscal_year: int
    month: int | None
    canonical_code: str
    schedule_tax_period_code: str | None
    display_name: str | None
    start_date: date
    end_date: date
    is_open: bool
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaxPeriodCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class TaxPeriodReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class TaxPeriodStatusHistoryOut(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    created_at: datetime
    reason: str | None
    from_is_open: bool
    to_is_open: bool
    origin: str

    model_config = {"from_attributes": True}
