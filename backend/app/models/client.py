import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PersonType(str, enum.Enum):
    NATURAL = "natural"
    JURIDICA = "juridica"


class RucStatus(str, enum.Enum):
    ACTIVO = "ACTIVO"
    BAJA_PROVISIONAL = "BAJA_PROVISIONAL"
    BAJA_DEFINITIVA = "BAJA_DEFINITIVA"
    BAJA_DE_OFICIO = "BAJA_DE_OFICIO"
    SUSPENSION_TEMPORAL = "SUSPENSION_TEMPORAL"


class FiscalAddressCondition(str, enum.Enum):
    HABIDO = "HABIDO"
    NO_HABIDO = "NO_HABIDO"
    NO_HALLADO = "NO_HALLADO"
    PENDIENTE = "PENDIENTE"


class TaxRegime(str, enum.Enum):
    NRUS = "NRUS"
    RER = "RER"
    RMT = "RMT"
    GENERAL = "GENERAL"
    # Marcador explicito de "regimen NO confirmado": ningun valor por defecto debe confundirse con un dato oficial de SUNAT.
    SIN_DETERMINAR = "SIN_DETERMINAR"


class IssuanceSystem(str, enum.Enum):
    MANUAL = "manual"
    ELECTRONICO = "electronico"
    MIXTO = "mixto"


class ServiceStatus(str, enum.Enum):
    PROSPECTO = "prospecto"
    ACTIVO = "activo"
    SUSPENDIDO = "suspendido"
    PENDIENTE_CIERRE = "pendiente_cierre"
    INACTIVO = "inactivo"


class Client(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ruc", name="uq_clients_tenant_ruc"),
        CheckConstraint(
            "payment_day IS NULL OR (payment_day BETWEEN 1 AND 31)",
            name="ck_clients_payment_day_range",
        ),
        # Auxiliar para FKs compuestas (id, tenant_id) desde Bloque 3 (ver
        # ADR-0025): declarations.client_id valida que el cliente pertenece
        # al mismo tenant, no solo que existe.
        UniqueConstraint("id", "tenant_id", name="uq_clients_id_tenant"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )

    ruc: Mapped[str] = mapped_column(String(11), nullable=False)
    ruc_last_digit: Mapped[int] = mapped_column(
        SmallInteger,
        Computed("CAST(SUBSTRING(ruc FROM 11 FOR 1) AS SMALLINT)", persisted=True),
        nullable=True,
    )

    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    person_type: Mapped[str] = mapped_column(String(20), nullable=False)
    taxpayer_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ruc_status: Mapped[str] = mapped_column(String(30), nullable=False)
    fiscal_address_condition: Mapped[str] = mapped_column(String(30), nullable=False)
    fiscal_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    ubigeo_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    department: Mapped[str | None] = mapped_column(String(60), nullable=True)
    province: Mapped[str | None] = mapped_column(String(60), nullable=True)
    district: Mapped[str | None] = mapped_column(String(60), nullable=True)

    main_economic_activity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secondary_economic_activities: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    tax_regime: Mapped[str] = mapped_column(String(20), nullable=False)
    activities_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issuance_system: Mapped[str | None] = mapped_column(String(20), nullable=True)

    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legal_representative_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legal_representative_doc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(255), nullable=True)
    whatsapp_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    is_good_taxpayer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    monthly_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    payment_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    onboarding_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    service_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ServiceStatus.PROSPECTO.value
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Columnas preparadas para la boveda de credenciales SOL (Fase 4).
    # No tienen logica de cifrado/descifrado implementada todavia: cualquier intento
    # de uso real debe lanzar NotImplementedError explicito. Ver docs/SECURITY_MODEL.md.
    sol_username_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sol_password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sol_cipher_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
