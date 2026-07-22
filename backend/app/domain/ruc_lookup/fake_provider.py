from datetime import UTC, datetime

from app.domain.ruc_lookup.provider import RucLookupResult
from app.services.ruc_validator import validate_ruc

_FICTITIOUS_BUSINESS_NAMES = [
    "COMERCIAL LOS ANDES SIMULADO S.A.C.",
    "SERVICIOS GENERALES DEMO E.I.R.L.",
    "INVERSIONES FICTICIAS DEL SUR S.A.",
    "DISTRIBUIDORA DE PRUEBA NORTE S.R.L.",
]

_FICTITIOUS_DISTRICTS = [
    ("LIMA", "LIMA", "MIRAFLORES"),
    ("LIMA", "LIMA", "SAN ISIDRO"),
    ("AREQUIPA", "AREQUIPA", "CERCADO"),
    ("LA LIBERTAD", "TRUJILLO", "TRUJILLO"),
]


class FakeRucLookupProvider:
    """Proveedor de consulta de RUC simulado para desarrollo y pruebas.

    Genera datos deterministas (misma entrada -> misma salida) sin ninguna
    llamada de red. Todo resultado se marca is_simulated=True. Nunca debe
    usarse en produccion; ver docs/INTEGRATIONS.md.
    """

    def lookup(self, ruc: str) -> RucLookupResult:
        validate_ruc(ruc)
        index = int(ruc) % len(_FICTITIOUS_BUSINESS_NAMES)
        department, province, district = _FICTITIOUS_DISTRICTS[
            int(ruc) % len(_FICTITIOUS_DISTRICTS)
        ]
        return RucLookupResult(
            ruc=ruc,
            business_name=_FICTITIOUS_BUSINESS_NAMES[index],
            trade_name=None,
            taxpayer_type=None,
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            fiscal_address=f"AV. SIMULADA {index + 100}, {district}",
            ubigeo_code=None,
            department=department,
            province=province,
            district=district,
            main_economic_activity="ACTIVIDAD ECONOMICA SIMULADA DE PRUEBA",
            secondary_economic_activities=[],
            queried_at=datetime.now(UTC),
            is_simulated=True,
        )
