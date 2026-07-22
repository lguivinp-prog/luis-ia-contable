import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.client import (
    FiscalAddressCondition,
    IssuanceSystem,
    PersonType,
    RucStatus,
    ServiceStatus,
    TaxRegime,
)


class ClientBase(BaseModel):
    ruc: str = Field(min_length=11, max_length=11)
    business_name: str = Field(min_length=1, max_length=255)
    trade_name: str | None = None
    person_type: PersonType
    taxpayer_type: str | None = None
    ruc_status: RucStatus
    fiscal_address_condition: FiscalAddressCondition
    fiscal_address: str | None = None
    ubigeo_code: str | None = None
    department: str | None = None
    province: str | None = None
    district: str | None = None
    main_economic_activity: str | None = None
    secondary_economic_activities: list[str] | None = None
    tax_regime: TaxRegime
    activities_start_date: date | None = None
    issuance_system: IssuanceSystem | None = None
    phone: str | None = None
    email: str | None = None
    legal_representative_name: str | None = None
    legal_representative_doc: str | None = None
    contact_person: str | None = None
    whatsapp_number: str | None = None
    monthly_fee: Decimal | None = None
    payment_day: int | None = Field(default=None, ge=1, le=31)
    onboarding_date: date | None = None
    notes: str | None = None
    tags: list[str] | None = None


class ClientCreate(ClientBase):
    # `is_good_taxpayer` es un campo heredado del Bloque 1 que ya no se
    # acepta en la entrada: se calcula dinamicamente desde
    # `client_tax_categories` (ver ADR-0013). extra="forbid" hace que enviarlo
    # produzca un 422 explicito en vez de ser ignorado en silencio.
    model_config = ConfigDict(extra="forbid")


class ClientUpdate(ClientBase):
    model_config = ConfigDict(extra="forbid")


class ClientStatusUpdate(BaseModel):
    service_status: ServiceStatus
    note: str = Field(min_length=1)


class ClientOut(ClientBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    ruc_last_digit: int | None
    service_status: ServiceStatus
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Calculado dinamicamente a la fecha de referencia "hoy" desde
    # `client_tax_categories` (ver ADR-0013); la columna `clients.is_good_taxpayer`
    # heredada del Bloque 1 nunca se lee para poblar este campo.
    is_good_taxpayer: bool

    model_config = ConfigDict(from_attributes=True)


class ClientListOut(BaseModel):
    items: list[ClientOut]
    total: int
    limit: int
    offset: int


class RucLookupOut(BaseModel):
    ruc: str
    business_name: str
    trade_name: str | None
    taxpayer_type: str | None
    ruc_status: str
    fiscal_address_condition: str
    fiscal_address: str | None
    ubigeo_code: str | None
    department: str | None
    province: str | None
    district: str | None
    main_economic_activity: str | None
    secondary_economic_activities: list[str]
    queried_at: datetime
    is_simulated: bool
