"""Resolucion de identidad mTLS (Fase 5, Bloque 5.2, Domain D -- ver
docs/phase5/block5_2/). Extrae la huella SHA-256 real del certificado de
cliente ya validado por la capa TLS (`ssl.CERT_REQUIRED` + CA de confianza
-- ver `server.py`) y la resuelve contra `sol_agents` (Domain B/C, unica
fuente de verdad de identidad de agente).

Autoridad final de autorizacion (seccion 20 del encargo): cadena de
certificado valida (ya garantizada por la capa TLS antes de que este codigo
se ejecute) Y huella conocida Y agente ACTIVE Y conexion viva. Ninguna de
estas condiciones sola es suficiente -- en particular, un certificado
criptograficamente valido de un agente REVOCADO se rechaza aqui, en la capa
de aplicacion, exactamente como exige la seccion 20."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.broker.errors import AgentRevokedError, AgentUnknownError, MtlsRequiredError
from app.repositories import sol_agent_access_repository as repo


@dataclass(frozen=True)
class AuthenticatedAgent:
    agent_id: uuid.UUID
    tenant_id: uuid.UUID
    certificate_fingerprint: str


def fingerprint_from_der(der_bytes: bytes) -> str:
    return hashlib.sha256(der_bytes).hexdigest()


def resolve_agent_identity(db: Session, *, certificate_der: bytes | None) -> AuthenticatedAgent:
    """`certificate_der is None` significa que la conexion TLS no presento
    certificado de cliente en absoluto -- solo posible si el llamador uso
    mal esta funcion contra el listener de pairing (`CERT_NONE`); el
    listener operacional (`CERT_REQUIRED`) nunca completa el handshake sin
    uno, asi que este caso es defensivo, no la barrera principal."""
    if certificate_der is None:
        raise MtlsRequiredError()

    fingerprint = fingerprint_from_der(certificate_der)
    agent = repo.get_agent_by_certificate_fingerprint(db, certificate_fingerprint=fingerprint)
    if agent is None:
        raise AgentUnknownError()
    if agent.status != "ACTIVE":
        raise AgentRevokedError()

    return AuthenticatedAgent(agent_id=agent.id, tenant_id=agent.tenant_id, certificate_fingerprint=fingerprint)
