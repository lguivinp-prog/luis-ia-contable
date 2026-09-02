"""Categorias de error de transporte del broker (Fase 5, Bloque 5.2, Domain D
-- ver docs/phase5/block5_2/). Estables, deliberadamente pocas y genericas:
nunca exponen un stack trace de TLS ni distinguen motivos que ayudarian a un
atacante a mapear el sistema (mismo principio ya aplicado por
`JobClaimInvalidError` en Domain C, seccion 42 del encargo de Domain C).

Cada codigo aqui es INTERNO al transporte (protocolo/conexion/autenticacion
de agente) -- nunca sustituye ni reimplementa los codigos de error ya
congelados de Domain C (`SOL_JOB_CLAIM_INVALID`, `SOL_PAIRING_*`, etc.), que
siguen siendo la unica autoridad para sus propios dominios."""

from __future__ import annotations


class BrokerTransportError(Exception):
    """Base de todas las categorias estables de error de transporte. `code`
    es el unico valor que cruza la red (nunca el mensaje de excepcion de
    Python ni un traceback)."""

    http_status: int = 400

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProtocolVersionUnsupportedError(BrokerTransportError):
    http_status = 400

    def __init__(self) -> None:
        super().__init__("PROTOCOL_VERSION_UNSUPPORTED")


class InvalidMessageError(BrokerTransportError):
    """Payload que no cumple el esquema estricto (campo desconocido, tipo
    invalido, campo faltante) -- nunca parseo best-effort (seccion 7 del
    encargo de Domain D)."""

    http_status = 422

    def __init__(self) -> None:
        super().__init__("INVALID_MESSAGE")


class MtlsRequiredError(BrokerTransportError):
    http_status = 401

    def __init__(self) -> None:
        super().__init__("MTLS_REQUIRED")


class AgentUnknownError(BrokerTransportError):
    http_status = 401

    def __init__(self) -> None:
        super().__init__("AGENT_UNKNOWN")


class AgentRevokedError(BrokerTransportError):
    http_status = 401

    def __init__(self) -> None:
        super().__init__("AGENT_REVOKED")


class CertificateInvalidError(BrokerTransportError):
    """Cubre CSR invalido, firma de CSR invalida, algoritmo/tamano de clave
    fuera de la lista permitida, extensiones peligrosas -- deliberadamente
    generico ante el agente (nunca revela cual validacion especifica
    fallo)."""

    http_status = 422

    def __init__(self) -> None:
        super().__init__("CERTIFICATE_INVALID")


class JobUnavailableError(BrokerTransportError):
    http_status = 404

    def __init__(self) -> None:
        super().__init__("JOB_UNAVAILABLE")


class ClaimRejectedError(BrokerTransportError):
    """Envoltura de transporte generica sobre cualquier rechazo de
    `sol_agent_job_service.claim_job` (token ya consumido/invalido, job no
    pendiente, agente no elegible) -- nunca distingue el motivo especifico
    ante el llamador de red, igual que `JobClaimInvalidError` no lo distingue
    ante el llamador de servicio."""

    http_status = 409

    def __init__(self) -> None:
        super().__init__("CLAIM_REJECTED")


class ConnectionReplacedError(BrokerTransportError):
    """Informativo -- la conexion anterior de este agente fue cerrada porque
    una nueva conexion mTLS valida del mismo agente reemplazo su entrada en
    el registro (Domain D, politica de a lo sumo 1 conexion viva por
    agente)."""

    http_status = 409

    def __init__(self) -> None:
        super().__init__("CONNECTION_REPLACED")


class BrokerShuttingDownError(BrokerTransportError):
    http_status = 503

    def __init__(self) -> None:
        super().__init__("BROKER_SHUTTING_DOWN")


class PayloadTooLargeError(BrokerTransportError):
    http_status = 413

    def __init__(self) -> None:
        super().__init__("PAYLOAD_TOO_LARGE")


class NotFoundError(BrokerTransportError):
    http_status = 404

    def __init__(self) -> None:
        super().__init__("NOT_FOUND")


class ConnectionNotEstablishedError(BrokerTransportError):
    """La identidad mTLS es valida, pero el agente nunca envio `AGENT_HELLO`
    en esta conexion (seccion 26 del encargo: "conexion viva" es un factor
    de autorizacion SEPARADO de un certificado valido -- ninguno de los dos
    solo es suficiente). El agente real siempre debe llamar
    `/agent/hello` antes de cualquier otra operacion del canal
    operacional."""

    http_status = 409

    def __init__(self) -> None:
        super().__init__("CONNECTION_NOT_ESTABLISHED")


class RequestNotAllowedError(BrokerTransportError):
    """Domain D.1, Gate 3: cubre Host inesperado, presencia de `Origin`,
    `Sec-Fetch-Site` indicando un contexto de navegador, header de
    protocolo nativo ausente, y `Content-Type` fuera de
    `application/json` -- deliberadamente el MISMO codigo generico para
    las cinco causas (nunca revela ante el llamador CUAL verificacion
    especifica fallo). Esta es defensa en profundidad sobre el listener
    loopback -- nunca sustituye mTLS ni el codigo de pairing (secciones
    19-20 del encargo)."""

    http_status = 403

    def __init__(self) -> None:
        super().__init__("REQUEST_NOT_ALLOWED")


class CredentialUnavailableError(BrokerTransportError):
    """Fase 5, Bloque 5.2, Domain E1-B: envoltura de transporte generica
    sobre CUALQUIER rechazo posterior a la carga tenant-safe de las
    relaciones job/sesion (grant ausente/vencido/ya `IN_FLIGHT`/de otro
    agente, vault bloqueada u operacion no autorizada, fallo de
    descifrado, o rechazo del gate de entrega de Domain C) -- nunca
    distingue el motivo especifico ante el agente, mismo principio
    no-oracle que `ClaimRejectedError`/`JobClaimInvalidError`."""

    http_status = 409

    def __init__(self) -> None:
        super().__init__("CREDENTIAL_UNAVAILABLE")


class ConnectionBusyError(BrokerTransportError):
    """Fase 5, Bloque 5.2, Domain E1-B: un `HELLO` de reemplazo llego
    mientras la conexion actual de este agente tiene un lease de entrega
    de secreto activo -- ver
    `app.broker.connection_registry.ConnectionBusyError`, que esta clase
    envuelve para el transporte HTTP."""

    http_status = 409

    def __init__(self) -> None:
        super().__init__("CONNECTION_BUSY")


class InternalError(BrokerTransportError):
    """Cubre unicamente una excepcion NO anticipada (bug) -- nunca expone
    stack trace ni el tipo/mensaje de la excepcion original ante el agente
    (seccion 65 del encargo)."""

    http_status = 500

    def __init__(self) -> None:
        super().__init__("INTERNAL_ERROR")
