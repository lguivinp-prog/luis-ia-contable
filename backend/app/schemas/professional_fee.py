import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProfessionalFeeAgreementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_type: str = Field(min_length=1, max_length=50)
    amount: Decimal = Field(ge=0)
    billing_day: int | None = Field(default=None, ge=1, le=31)
    included_services: str | None = None
    description: str | None = None
    valid_from: date
    valid_until: date | None = None
    observations: str | None = None


class ProfessionalFeeAgreementOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    service_type: str
    amount: Decimal
    currency: str
    billing_day: int | None
    included_services: str | None
    description: str | None
    status: str
    valid_from: date
    valid_until: date | None
    observations: str | None

    model_config = {"from_attributes": True}


class ProfessionalFeeAgreementLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_date: date | None = None
    reason: str = Field(min_length=1)


class ProfessionalFeeChargeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tax_period_id: uuid.UUID
    amount: Decimal | None = Field(default=None, ge=0)
    due_date: date | None = None
    observations: str | None = None


class ProfessionalFeeChargeIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = None


class ProfessionalFeeChargeOut(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    tax_period_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    due_date: date | None
    issued_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    observations: str | None

    model_config = {"from_attributes": True}


class ProfessionalFeePaymentCreate(BaseModel):
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


class ProfessionalFeePaymentVoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class ProfessionalFeePaymentOut(BaseModel):
    id: uuid.UUID
    charge_id: uuid.UUID
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


class ProfessionalReceiptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_type: str = Field(min_length=1, max_length=20)
    number: str = Field(min_length=1, max_length=50)
    issued_at: date
    amount: Decimal = Field(gt=0)
    document_reference: str | None = None
    observations: str | None = None


class ProfessionalReceiptVoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class ProfessionalReceiptOut(BaseModel):
    id: uuid.UUID
    charge_id: uuid.UUID
    issued_by: uuid.UUID
    receipt_type: str
    number: str
    issued_at: date
    amount: Decimal
    currency: str
    status: str
    voided_at: datetime | None
    void_reason: str | None
    document_reference: str | None
    observations: str | None

    model_config = {"from_attributes": True}
