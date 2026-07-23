import enum
import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DeclarationHistoryMixin, TimestampMixin, UUIDPrimaryKeyMixin

# Regex estricta (ver ADR-0016): el grupo (0[1-9]|1[0-2]) acepta unicamente
# 01-12, rechazando 00/13-99 directamente a nivel de base de datos.
_SCHEDULE_TAX_PERIOD_CODE_CHECK = (
    "schedule_tax_period_code IS NULL OR "
    "schedule_tax_period_code ~ '^[0-9]{4}(-(0[1-9]|1[0-2]))?$'"
)

# Canonicidad completa por tipo de periodo (ver "Periodos tributarios
# canonicos" del plan / ADR-0016): una insercion SQL directa no puede crear un
# periodo internamente incoherente. Solo funciones inmutables (make_date,
# concatenacion, aritmetica de intervalos), permitidas en CHECK.
_PERIOD_CANONICITY_CHECK = """
(
  (period_type = 'MONTHLY' AND
   fiscal_year BETWEEN 2000 AND 2100 AND
   canonical_code = fiscal_year::text || '-' || LPAD(month::text, 2, '0') AND
   schedule_tax_period_code = canonical_code AND
   start_date = make_date(fiscal_year, month, 1) AND
   end_date = (make_date(fiscal_year, month, 1) + INTERVAL '1 month' - INTERVAL '1 day')::date)
  OR
  (period_type = 'ANNUAL' AND
   fiscal_year BETWEEN 2000 AND 2100 AND
   month IS NULL AND
   canonical_code = fiscal_year::text AND
   schedule_tax_period_code = canonical_code AND
   start_date = make_date(fiscal_year, 1, 1) AND
   end_date = make_date(fiscal_year, 12, 31))
  OR
  (period_type = 'SPECIAL' AND
   canonical_code <> '' AND
   end_date >= start_date AND
   (schedule_tax_period_code IS NULL OR LEFT(schedule_tax_period_code, 4)::int = fiscal_year))
)
""".strip()


class TaxPeriodType(str, enum.Enum):
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"
    SPECIAL = "SPECIAL"


class TaxPeriod(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Periodo tributario con identidad canonica (ver ADR-0016). La unicidad
    real vive en los 3 indices unicos parciales por tipo, nunca en un texto
    libre; canonical_code/schedule_tax_period_code se derivan/validan en el
    servicio y se refuerzan con el CHECK de canonicidad completa."""

    __tablename__ = "tax_periods"
    __table_args__ = (
        sa.UniqueConstraint("id", "tenant_id", name="uq_tax_periods_id_tenant"),
        Index(
            "ux_tax_periods_monthly_identity",
            "tenant_id",
            "fiscal_year",
            "month",
            unique=True,
            postgresql_where=sa.text("period_type = 'MONTHLY'"),
        ),
        Index(
            "ux_tax_periods_annual_identity",
            "tenant_id",
            "fiscal_year",
            unique=True,
            postgresql_where=sa.text("period_type = 'ANNUAL'"),
        ),
        Index(
            "ux_tax_periods_special_identity",
            "tenant_id",
            "canonical_code",
            unique=True,
            postgresql_where=sa.text("period_type = 'SPECIAL'"),
        ),
        CheckConstraint(
            _SCHEDULE_TAX_PERIOD_CODE_CHECK, name="ck_tax_periods_schedule_code_format"
        ),
        CheckConstraint(_PERIOD_CANONICITY_CHECK, name="ck_tax_periods_canonicity"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    canonical_code: Mapped[str] = mapped_column(String(50), nullable=False)
    schedule_tax_period_code: Mapped[str | None] = mapped_column(String(7), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_display(self) -> str:
        return self.display_name or self.canonical_code


class TaxPeriodStatusHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "tax_period_status_history"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"],
            ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_tax_period_status_history_period_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_period_status_history_actor_tenant",
        ),
    )

    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    to_is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
