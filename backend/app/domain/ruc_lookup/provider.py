from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RucLookupResult:
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
    """True cuando el resultado proviene de un proveedor de desarrollo/pruebas,
    nunca de una fuente real. El frontend debe mostrar esto de forma visible."""


class RucLookupProvider(Protocol):
    """Puerto de dominio para consultar datos publicos de un RUC.

    Cualquier adaptador (simulado o real) debe implementar este contrato.
    Ver docs/DECISIONS/ADR-0007-ruc-lookup-abstraction.md.
    """

    def lookup(self, ruc: str) -> RucLookupResult: ...
