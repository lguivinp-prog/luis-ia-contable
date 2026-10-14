"""M3 -- CATALOGO UNICO de destinos de SOL que el producto sabe pedir (backend). Fuente de verdad de: la allowlist del servicio, la lista de
destinos habilitados que ve la UI y las politicas de cada destino. El agente mantiene su propio catalogo estructural
(`windows_agent.destination_contract.CONTRACTS`) y una prueba exige que ambos coincidan en ids y en estado de validacion.

REGLAS (no negociables):
* Un destino solo esta HABILITADO si su validacion es `LIVE_VALIDATED`: existe evidencia estructural REAL (reconocimiento supervisado por el
  propietario) y pruebas de identidad positivas y negativas. Un destino `NOT_VALIDATED` es CONOCIDO pero esta DESHABILITADO: el backend lo
  rechaza (`SOL_DESTINATION_DISABLED`) antes de crear sesion, job o abrir el agente; la UI solo informa su estado.
* Abrir un destino es un evento de ACCESO: nunca implica presentar, pagar, rectificar ni modificar nada, ni se registra como cumplimiento.
* Nada de URLs, selectores ni scripts viaja del frontend: solo el identificador estable de este catalogo."""

from __future__ import annotations

from dataclasses import dataclass

LIVE_VALIDATED = "LIVE_VALIDATED"
NOT_VALIDATED = "NOT_VALIDATED"


@dataclass(frozen=True)
class DestinationPolicy:
    id: str
    label: str
    validation: str
    group: str  # CORE | TAX_TOOL
    extraction: bool = False  # solo FICHA_RUC puede extraer datos (con propuesta de revision humana)
    # Evidencia estructural REAL del reconocimiento: COMPLETE (entrada + identidad de llegada, validada) | ARRIVAL_ONLY (solo la identidad de llegada;
    # falta la entrada de menu) | ALIAS (es OTRO destino ya validado) | CONTAINED (hoja dentro de otro destino) | NONE (sin observacion)
    evidence: str = "NONE"  # (ver arriba; RECON_COMPLETE = contrato completo con evidencia real, aun sin aceptacion en vivo)
    alias_of: str | None = None  # mismo destino funcional que otro (p. ej. Tramites y Consultas == Menu SOL): se resuelve, no se duplica
    contained_in: str | None = None  # se alcanza DESDE otro destino (Declara Facil esta dentro de la plataforma de Declaraciones y Pagos)
    open_only: bool = False  # solo apertura supervisada: toda operacion posterior queda bajo control humano
    superseded_by: tuple[str, ...] = ()  # identificador historico reemplazado por destinos mas especificos (no se ofrece)
    # La ESTRUCTURA del portal esta validada en vivo (catalogo), pero la autorizacion de una credencial NO se extrapola a otros contribuyentes, perfiles ni
    # tenants: cada perfil debe haber superado su PROPIA aceptacion (PASS del propietario) o contar con una autorizacion explicita de prueba. Ver
    # `sol_destination_acceptance_service.access_gate`. MENU_SOL y FICHA_RUC (M2) conservan su regla: habilitados para todo perfil utilizable.
    requires_profile_proof: bool = False

    @property
    def enabled(self) -> bool:
        return self.validation == LIVE_VALIDATED

    @property
    def offered(self) -> bool:
        """Se muestra como tarjeta propia en la UI: ni alias, ni contenido en otro destino, ni reemplazado."""
        return self.alias_of is None and self.contained_in is None and not self.superseded_by


CATALOG: tuple[DestinationPolicy, ...] = (
    DestinationPolicy("MENU_SOL", "Menú SOL", LIVE_VALIDATED, "CORE"),
    DestinationPolicy("FICHA_RUC", "Ficha RUC", LIVE_VALIDATED, "CORE", extraction=True),
    # Trámites y Consultas ES el acceso al Menú SOL completo según las autorizaciones de la credencial (tarjeta pública -> login -> Menú SOL).
    DestinationPolicy("TRAMITES_Y_CONSULTAS", "Trámites y Consultas", LIVE_VALIDATED, "CORE", evidence="ALIAS", alias_of="MENU_SOL"),
    # M3: conocidos pero DESHABILITADOS hasta contar con entrada de menu + identidad de llegada reales (ver ADR-0076).
    DestinationPolicy("SIRE", "SIRE", NOT_VALIDATED, "TAX_TOOL", superseded_by=("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS")),
    # LIVE_ACCEPTED: reconocimiento real (entrada de menu + llegada vinculada) Y aceptacion en vivo del agente por el propietario (14/10/2026): la
    # estructura del portal queda validada. El acceso por perfil exige su propia prueba (`requires_profile_proof`).
    DestinationPolicy("SIRE_RVIE", "SIRE · Registro de Ventas (RVIE)", LIVE_VALIDATED, "TAX_TOOL", evidence="LIVE_ACCEPTED", requires_profile_proof=True),
    DestinationPolicy("SIRE_RCE", "SIRE · Registro de Compras (RCE)", LIVE_VALIDATED, "TAX_TOOL", evidence="LIVE_ACCEPTED", requires_profile_proof=True),
    DestinationPolicy("SIRE_GENERACION_REGISTROS", "SIRE · Generación de Registros Electrónicos", NOT_VALIDATED, "TAX_TOOL", evidence="RECON_COMPLETE", open_only=True),
    DestinationPolicy("DECLARACIONES_Y_PAGOS", "Declaraciones y Pagos", LIVE_VALIDATED, "TAX_TOOL", evidence="LIVE_ACCEPTED", requires_profile_proof=True),
    DestinationPolicy("DECLARA_FACIL", "Declara Fácil", NOT_VALIDATED, "TAX_TOOL", evidence="CONTAINED", contained_in="DECLARACIONES_Y_PAGOS"),
)

# Operaciones que NINGUN destino automatiza jamas (el agente se detiene al validar la llegada; el humano conduce todo lo demas).
FORBIDDEN_AUTOMATION = ("UPLOAD_FILE", "GENERATE_RECORDS", "SUBMIT", "SEND", "DECLARE", "PAY", "RECTIFY")

# Fuera de alcance de M3 (jamas se ofrecen): Buzon SUNAT y todo lo que presente, pague, rectifique o modifique.
EXCLUDED_FROM_SCOPE = frozenset({"BUZON_SUNAT", "SUNAFIL"})


def _by_id() -> dict[str, DestinationPolicy]:
    return {d.id: d for d in CATALOG}


def all_ids() -> tuple[str, ...]:
    return tuple(d.id for d in CATALOG)


def is_known(destination: str | None) -> bool:
    return destination in _by_id()


def resolve(destination: str | None) -> str | None:
    """Un alias se resuelve al destino funcional que representa (Tramites y Consultas -> Menu SOL); el resto se devuelve igual."""
    policy = _by_id().get(destination or "")
    return policy.alias_of if policy is not None and policy.alias_of else destination


def is_enabled(destination: str | None) -> bool:
    policy = _by_id().get(resolve(destination) or "")
    return policy is not None and policy.enabled


def policy_of(destination: str) -> DestinationPolicy | None:
    return _by_id().get(destination)


def enabled_ids() -> list[str]:
    """Destinos habilitados que se ofrecen como acceso propio (los alias se resuelven y no se listan dos veces)."""
    return [d.id for d in CATALOG if d.enabled and d.alias_of is None]
