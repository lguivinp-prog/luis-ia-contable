"""Ciclo de vida del pairing de un agente Windows (Fase 5, Bloque 5.2,
Domain C -- ver docs/phase5/block5_2/, Domain A.1/A.2/A.3). Ningun metodo de
este servicio ejecuta `db.commit()` (ADR-0037, mismo patron que
sol_vault_service.py) salvo el `db.rollback()` puntual y documentado ante
`IntegrityError` de una carrera de confirmacion (ver `confirm_pairing`,
mismo patron ya usado en `google_oauth_service.py`).

No implementa: transporte de red, broker, mTLS, emision/firma de CSR, ni
Windows Agent -- fuera de alcance de Domain C (ver
BLOCK5_2_ARCHITECTURE.md)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.sol_pairing_crypto import compute_pairing_verifier, generate_pairing_code, verify_pairing_code
from app.core.sol_pairing_verification_throttle import PairingVerificationRateLimitedError
from app.core import sol_pairing_verification_throttle as verification_throttle
from app.models.audit_log import AuditAction
from app.models.sol_agent_access import SolAgent, SolAgentPairing
from app.repositories import sol_agent_access_repository as repo
from app.services import audit_service
from app.services import sol_agent_pairing_rate_limit_service as rate_limit_service

PAIRING_TTL_SECONDS = 120
PAIRING_MAX_FAILED_ATTEMPTS = 5


class SolAgentPairingServiceError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class PairingNotFoundError(SolAgentPairingServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_PAIRING_NOT_FOUND")


class PairingAlreadyPendingError(SolAgentPairingServiceError):
    def __init__(self) -> None:
        super().__init__(
            "SOL_PAIRING_ALREADY_PENDING", "Ya existe un pairing PENDING para este usuario"
        )


class PairingCreationRateLimitedError(SolAgentPairingServiceError):
    """Segunda capa (Domain C.1, Gate 3) -- distinta del lock durable de 5
    fallos DENTRO de un pairing (`PairingLockedError`). Nunca revela el
    codigo/verificador en el mensaje."""

    def __init__(self, locked_until) -> None:
        super().__init__(
            "SOL_PAIRING_CREATION_RATE_LIMITED",
            f"Demasiados intentos de pairing recientes; reintente despues de {locked_until.isoformat()}",
        )
        self.locked_until = locked_until


class PairingNotPendingError(SolAgentPairingServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_PAIRING_NOT_PENDING")


class PairingExpiredError(SolAgentPairingServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_PAIRING_EXPIRED")


class PairingLockedError(SolAgentPairingServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_PAIRING_LOCKED")


class PairingInvalidCodeError(SolAgentPairingServiceError):
    def __init__(self) -> None:
        # Nunca incluye el codigo/verificador en el mensaje -- Domain A.3
        # seccion 10 / encargo de Domain C seccion 10.
        super().__init__("SOL_PAIRING_INVALID_CODE")


class AgentCertificateFingerprintConflictError(SolAgentPairingServiceError):
    def __init__(self) -> None:
        super().__init__(
            "SOL_AGENT_CERTIFICATE_FINGERPRINT_CONFLICT",
            "Ya existe un agente con esa identidad criptografica",
        )


@dataclass
class PairingCreateResult:
    """`pairing_code` es texto plano, disponible SOLO aqui -- nunca se
    vuelve a poder leer (no se persiste, ver Domain A.3). `__repr__`
    redactado para que un log/trace/`pytest -l` no lo revele por
    accidente (encargo de Domain C, seccion 14/40)."""

    pairing: SolAgentPairing
    pairing_code: str

    def __repr__(self) -> str:
        return f"PairingCreateResult(pairing_id={self.pairing.id!r}, pairing_code='***REDACTED***')"


def get_pairing(db: Session, *, tenant_id: uuid.UUID, pairing_id: uuid.UUID) -> SolAgentPairing:
    pairing = repo.get_pairing(db, tenant_id=tenant_id, pairing_id=pairing_id)
    if pairing is None:
        raise PairingNotFoundError()
    return pairing


def create_pairing(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    agent_display_name_candidate: str,
) -> PairingCreateResult:
    """Falla cerrado ANTES de crear ninguna fila si `PAIRING_VERIFIER_KEY`
    no esta configurada (`compute_pairing_verifier` lanza
    `PairingVerifierKeyMisconfiguredError`, ver sol_pairing_crypto.py) --
    nunca un pairing con verificador vacio/falso.

    Aplica la segunda capa de rate limit (Domain C.1, Gate 3) ANTES de
    tocar cualquier fila -- si (tenant, requester) esta en backoff por
    crear demasiados pairings recientes, ni siquiera se genera codigo."""
    lockout = rate_limit_service.get_pairing_creation_lockout_status(
        db, tenant_id=tenant_id, requested_by_user_id=requested_by_user_id
    )
    if lockout.is_locked:
        raise PairingCreationRateLimitedError(lockout.locked_until)

    now = clock.db_now(db)
    existing = repo.get_pending_pairing_for_requester(
        db, tenant_id=tenant_id, requested_by_user_id=requested_by_user_id
    )
    if existing is not None:
        if existing.expires_at <= now:
            existing.status = "EXPIRED"
            existing.pairing_code_verifier = None
            existing.version += 1
            db.flush()
            verification_throttle.evict(tenant_id=tenant_id, pairing_id=existing.id)
        else:
            raise PairingAlreadyPendingError()

    pairing_id = uuid.uuid4()
    code = generate_pairing_code()
    verifier = compute_pairing_verifier(tenant_id=tenant_id, pairing_id=pairing_id, code=code)

    pairing = SolAgentPairing(
        id=pairing_id,
        tenant_id=tenant_id,
        requested_by_user_id=requested_by_user_id,
        agent_display_name_candidate=agent_display_name_candidate,
        pairing_code_verifier=verifier,
        status="PENDING",
        failed_attempt_count=0,
        # `created_at` se fija explicitamente con el mismo `clock.db_now(db)`
        # que `expires_at` (nunca el `server_default=now()` de la columna,
        # que dispararia una consulta SEPARADA a Postgres en un instante
        # ligeramente distinto): ambas alimentan
        # `ck_sol_agent_pairings_expires_after_created`, que exige coherencia
        # entre ambas -- un segundo `now()` diferente, por minimo que sea el
        # desfase, podria violarla bajo carga.
        created_at=now,
        expires_at=now + timedelta(seconds=PAIRING_TTL_SECONDS),
    )
    db.add(pairing)
    db.flush()
    return PairingCreateResult(pairing=pairing, pairing_code=code)


def confirm_pairing(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    pairing_id: uuid.UUID,
    code: str,
    issue_certificate: Callable[[], str],
    actor_user_id: uuid.UUID | None,
) -> SolAgentPairing:
    """Confirmacion atomica: bloquea la fila (`SELECT ... FOR UPDATE`, ver
    `get_pairing_for_update`) antes de verificar el codigo, y crea el
    `SolAgent` + completa el pairing dentro de la MISMA transaccion. Dos
    intentos de confirmacion simultaneos sobre el mismo pairing quedan
    serializados por ese lock -- el segundo, tras esperar, encuentra
    `status` ya distinto de `PENDING` y falla determinísticamente (ver
    `test_sol_agent_access_service_concurrency.py`).

    `issue_certificate` es un callback de aridad cero, provisto por un
    componente confiable (broker) -- este servicio NUNCA genera, firma, ni
    deriva material criptografico de certificado el mismo (encargo de
    Domain C, seccion 16 / Domain D.1, Gate 1). Se invoca UNICAMENTE
    despues de que el codigo de pairing fue verificado como correcto --
    nunca antes: un pairing invalido, vencido, LOCKED, bloqueado por rate
    limit, o con codigo incorrecto NUNCA dispara este callback (Domain D.1,
    seccion 1: "un pairing invalido/no confirmado no debe provocar una
    firma con la CA"). Debe devolver la huella (`certificate_fingerprint`)
    del certificado ya firmado -- cualquier excepcion que lance se propaga
    sin modificar (este metodo no ha mutado la fila todavia en ese punto,
    asi que un rollback del llamador es seguro y completo).

    Domain C.1b, Gate residual: el lookup de la fila (tenant-scoped,
    `SELECT ... FOR UPDATE`) ocurre ANTES del throttle process-local de
    verificacion -- nunca al reves. Ese orden es deliberado (Domain C.1b,
    seccion 3): el throttle SOLO registra una entrada para un pairing que
    ya se confirmo existente, del tenant correcto, `PENDING` y no vencido
    -- un `pairing_id` arbitrario/inexistente/ajeno nunca llega a crear
    estado en el diccionario process-local (ver
    `sol_pairing_verification_throttle.py` para el porque). El lookup en
    si no muta, no incrementa `failed_attempt_count`, ni verifica codigo
    -- salvo la transicion a `EXPIRED` ya congelada (que tampoco toca el
    codigo). Una solicitud rechazada por el throttle NUNCA llega a
    `verify_pairing_code` ni a `failed_attempt_count` -- ni siquiera se
    revela si el codigo presentado era correcto (reduce oracle behavior).
    Distinto del lock durable de 5 fallos (autoridad final, en PostgreSQL)
    y del rate limit de CREACION de pairings
    (`sol_agent_pairing_rate_limit_service.py`, tambien en BD) -- ninguno
    de los dos se toca aqui."""
    now = clock.db_now(db)
    pairing = repo.get_pairing_for_update(db, tenant_id=tenant_id, pairing_id=pairing_id)
    if pairing is None:
        raise PairingNotFoundError()
    if pairing.status == "LOCKED":
        raise PairingLockedError()
    if pairing.status != "PENDING":
        raise PairingNotPendingError()
    if pairing.expires_at <= now:
        pairing.status = "EXPIRED"
        pairing.pairing_code_verifier = None
        pairing.version += 1
        db.flush()
        verification_throttle.evict(tenant_id=tenant_id, pairing_id=pairing.id)
        raise PairingExpiredError()

    # Solo a partir de aqui el pairing quedo confirmado como valido
    # (existe, tenant-safe, PENDING, no vencido) -- recien ahora se
    # registra/consulta el throttle process-local.
    verification_throttle.check_and_register_attempt(tenant_id=tenant_id, pairing_id=pairing.id)

    if not verify_pairing_code(
        tenant_id=tenant_id, pairing_id=pairing.id, code=code, stored_verifier=pairing.pairing_code_verifier
    ):
        pairing.failed_attempt_count += 1
        if pairing.failed_attempt_count >= PAIRING_MAX_FAILED_ATTEMPTS:
            pairing.status = "LOCKED"
            pairing.locked_at = now
            pairing.pairing_code_verifier = None
            pairing.version += 1
            db.flush()
            verification_throttle.evict(tenant_id=tenant_id, pairing_id=pairing.id)
            audit_service.record(
                db,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                action=AuditAction.PAIRING_LOCKED,
                entity_type="sol_agent_pairing",
                entity_id=pairing.id,
                metadata={"failed_attempt_count": pairing.failed_attempt_count},
            )
            raise PairingLockedError()
        pairing.version += 1
        db.flush()
        raise PairingInvalidCodeError()

    # Solo aqui, con el codigo YA verificado como correcto (Domain D.1, Gate
    # 1), se autoriza la emision del certificado -- la firma criptografica
    # real ocurre dentro de este callback, todavia dentro de la misma
    # transaccion/lock de fila que el resto de esta operacion.
    certificate_fingerprint = issue_certificate()

    if repo.get_agent_by_certificate_fingerprint(db, certificate_fingerprint=certificate_fingerprint) is not None:
        raise AgentCertificateFingerprintConflictError()

    agent = SolAgent(
        tenant_id=tenant_id,
        display_name=pairing.agent_display_name_candidate,
        certificate_fingerprint=certificate_fingerprint,
        status="ACTIVE",
        paired_at=now,
    )
    db.add(agent)
    try:
        db.flush()
    except IntegrityError:
        # Backstop de BD ante una carrera de `certificate_fingerprint` que la
        # comprobacion proactiva de arriba no alcanzo a ver (mismo patron ya
        # productivo en google_oauth_service.py, Seccion 21) -- el servicio
        # SI controla el rollback aqui, deliberadamente, porque es el unico
        # punto en que este modulo recupera una transaccion de un fallo real
        # de BD en vez de dejarla abortada para el llamador.
        db.rollback()
        raise AgentCertificateFingerprintConflictError() from None

    pairing.status = "COMPLETED"
    pairing.paired_at = now
    pairing.confirmed_agent_id = agent.id
    pairing.pairing_code_verifier = None
    pairing.version += 1
    db.flush()
    verification_throttle.evict(tenant_id=tenant_id, pairing_id=pairing.id)

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=AuditAction.PAIRING_COMPLETED,
        entity_type="sol_agent_pairing",
        entity_id=pairing.id,
        metadata={"confirmed_agent_id": str(agent.id)},
    )
    return pairing


def cancel_pairing(db: Session, *, tenant_id: uuid.UUID, pairing_id: uuid.UUID) -> SolAgentPairing:
    """Idempotente: `PENDING -> CANCELLED`; ya `CANCELLED` es un no-op
    exitoso; cualquier otro estado terminal (`COMPLETED`/`LOCKED`/`EXPIRED`)
    nunca se reescribe."""
    pairing = repo.get_pairing_for_update(db, tenant_id=tenant_id, pairing_id=pairing_id)
    if pairing is None:
        raise PairingNotFoundError()
    if pairing.status == "CANCELLED":
        return pairing
    if pairing.status != "PENDING":
        raise PairingNotPendingError()

    pairing.status = "CANCELLED"
    pairing.pairing_code_verifier = None
    pairing.version += 1
    db.flush()
    verification_throttle.evict(tenant_id=tenant_id, pairing_id=pairing.id)
    return pairing


def expire_pairing(db: Session, *, tenant_id: uuid.UUID, pairing_id: uuid.UUID) -> SolAgentPairing:
    """Operacion explicita (sin scheduler en Domain C, ver seccion 19 del
    encargo): solo actua sobre un pairing `PENDING` cuyo `expires_at` ya
    paso segun el reloj autoritativo del backend."""
    now = clock.db_now(db)
    pairing = repo.get_pairing_for_update(db, tenant_id=tenant_id, pairing_id=pairing_id)
    if pairing is None:
        raise PairingNotFoundError()
    if pairing.status == "EXPIRED":
        return pairing
    if pairing.status != "PENDING":
        raise PairingNotPendingError()
    if pairing.expires_at > now:
        raise SolAgentPairingServiceError(
            "SOL_PAIRING_NOT_YET_EXPIRED", "El pairing todavia no vencio su TTL"
        )

    pairing.status = "EXPIRED"
    pairing.pairing_code_verifier = None
    pairing.version += 1
    db.flush()
    verification_throttle.evict(tenant_id=tenant_id, pairing_id=pairing.id)
    return pairing
