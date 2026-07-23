from decimal import Decimal

from pydantic import BaseModel

from app.schemas.declaration import DeclarationOut


class DashboardRow(DeclarationOut):
    # Agregado tributario 1:1 por declaration_id -- ver app/services/dashboard_service.py.
    tax_gross_amount: Decimal | None = None
    tax_total_payable: Decimal | None = None
    tax_total_paid: Decimal = Decimal("0")
    missing_documents_count: int = 0

    # Agregado de honorarios por (client_id, tax_period_id), repetido (nunca
    # multiplicado) en cada declaracion de ese cliente/periodo -- ver
    # app/services/dashboard_service.py.
    fee_total_charges: int = 0
    fee_total_amount: Decimal = Decimal("0")
    fee_total_paid: Decimal = Decimal("0")
    fee_balance: Decimal = Decimal("0")
    fee_pending_charges_count: int = 0
    fee_worst_collection_risk: str | None = None


class DashboardOut(BaseModel):
    items: list[DashboardRow]
    total: int
    limit: int
    offset: int
