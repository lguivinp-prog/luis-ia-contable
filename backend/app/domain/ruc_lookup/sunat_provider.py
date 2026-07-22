from app.domain.ruc_lookup.provider import RucLookupResult

_NOT_IMPLEMENTED_MESSAGE = (
    "Proveedor real de consulta de RUC aun no implementado. "
    "Ver docs/ROADMAP.md e docs/INTEGRATIONS.md (Fase 1, bloques posteriores)."
)


class SunatRucLookupProvider:
    """Adaptador real de consulta de RUC (pendiente de implementacion).

    Deliberadamente NO simula datos: cualquier uso debe fallar de forma
    explicita hasta que se implemente un adaptador real con token propio,
    cache, control de cuota y reintentos (ver ADR-0007).
    """

    def lookup(self, ruc: str) -> RucLookupResult:
        raise NotImplementedError(_NOT_IMPLEMENTED_MESSAGE)
