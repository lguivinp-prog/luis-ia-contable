import uuid
from datetime import date

from sqlalchemy import Boolean as SABoolean
from sqlalchemy import CheckConstraint, Computed, Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import DATERANGE, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TaxCategoryCatalog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Catalogo de categorias tributarias del cliente (ver ADR-0013).

    Desacoplado de `clients.tax_regime` (que permanece exclusivo para
    NRUS/RER/RMT/GENERAL). Soporta codigos como BUEN_CONTRIBUYENTE,
    ENTIDAD_PUBLICA, CATEGORIA_ESPECIAL_*, PRORROGA_TERRITORIAL_* sin
    requerir cambios de esquema futuros.
    """

    __tablename__ = "tax_category_catalog"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(SABoolean, nullable=False, default=True)


class ClientTaxCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Asignacion de una categoria a un cliente, con vigencia (historial permitido).

    `validity_range` es una columna generada por PostgreSQL a partir de
    (valid_from, valid_until); la restriccion de exclusion (requiere la
    extension btree_gist) impide vigencias solapadas para el mismo
    cliente+categoria, mientras permite renovaciones no solapadas.
    """

    __tablename__ = "client_tax_categories"
    __table_args__ = (
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_client_tax_categories_valid_range",
        ),
        ExcludeConstraint(
            ("client_id", "="),
            ("category_code", "="),
            ("validity_range", "&&"),
            using="gist",
            name="ex_client_tax_categories_no_overlap",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    category_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("tax_category_catalog.code"), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Columna generada por PostgreSQL (GENERATED ALWAYS AS ... STORED), usada
    # unicamente por la restriccion de exclusion; la app nunca la escribe.
    validity_range: Mapped[object] = mapped_column(
        DATERANGE,
        Computed("daterange(valid_from, valid_until, '[]')", persisted=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
