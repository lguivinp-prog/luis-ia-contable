import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TaxPaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    amount: Decimal = Field(gt=0)
    paid_at: date
    payment_method: str = Field(min_length=1, max_length=30)
    operation_number: str | None = None
    reference: str | None = None
    observations: str | None = None
    overpayment_confirmed: bool = False
    overpayment_reason: str | None = None


class TaxPaymentVoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class TaxPaymentOut(BaseModel):
    id: uuid.UUID
    declaration_id: uuid.UUID
    assessment_id: uuid.UUID
    paid_at: date
    amount: Decimal
    payment_method: str
    operation_number: str | None
    reference: str | None
    observations: str | None
    overpayment_confirmed: bool
    overpayment_reason: str | None
    overpayment_amount: Decimal | None
    is_voided: bool
    voided_at: datetime | None
    void_reason: str | None
    idempotency_key: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}
