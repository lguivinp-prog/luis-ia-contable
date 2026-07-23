import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TaxAssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_tax_amount: Decimal = Field(ge=0)
    credits_applied: Decimal = Field(default=Decimal("0"), ge=0)
    observations: str | None = None


class TaxAssessmentCorrectionCreate(TaxAssessmentCreate):
    reason: str = Field(min_length=1)


class TaxAssessmentOut(BaseModel):
    id: uuid.UUID
    declaration_id: uuid.UUID
    assessment_type: str
    sequence_number: int
    gross_tax_amount: Decimal
    credits_applied: Decimal
    total_payable: Decimal
    remaining_credit: Decimal
    currency: str
    assessed_at: datetime
    assessed_by: uuid.UUID
    observations: str | None
    is_current: bool

    model_config = {"from_attributes": True}
