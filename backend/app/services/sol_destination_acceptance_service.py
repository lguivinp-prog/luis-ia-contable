"""M3 -- aceptacion real controlada de destinos SOL (ver `app.models.sol_destination_acceptance`).

Reglas (fallo cerrado):
* Solo un destino `RECON_COMPLETE`, ofrecido, no habilitado y NO `open_only` puede probarse (Generacion de Registros queda MANUAL_ONLY).
* La autorizacion pertenece a UN tenant, UN cliente, UN perfil de credencial, UN destino y UN usuario (el propietario que la concede es quien debe iniciar
  la sesion); vence rapido y se consume de forma ATOMICA (fila bloqueada) al crear la sesion: no se reutiliza ni se comparte entre clientes/perfiles.
* No habilita el destino para nadie mas: `is_enabled` no cambia. La habilitacion global solo ocurre por codigo y tests tras una prueba real PASS.
* PASS exige que la sesion consumidora haya llegado de verdad al destino (resultado DESTINATION_REACHED) ademas de la confirmacion del propietario."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock, sunat_destinations
from app.broker.protocol import DIAGNOSTIC_CODES
from app.models.audit_log import AuditAction, AuditLog
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession
from app.models.sol_destination_acceptance import SolDestinationAcceptance
from app.broker.session_authorization import USABLE_CREDENTIAL_STATUSES
from app.repositories import client_repository
from app.services import audit_service

ACCEPTANCE_TTL_SECONDS = 30 * 60
REACHED_RESULTS = {"DESTINATION_REACHED"}
NOTE_MAX = 300


class AcceptanceError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


PROVABLE_EVIDENCE = frozenset({"RECON_COMPLETE", "LIVE_ACCEPTED"})
GATE_OPEN = "OPEN"  # puede abrirse sin autorizacion adicional
GATE_NEEDS_GRANT = "NEEDS_GRANT"  # exige una autorizacion explicita de prueba del propietario (un solo uso)
GATE_DISABLED = "DISABLED"  # no existe forma de abrirlo


def is_eligible(destination: str | None) -> bool:
    """Candidato a prueba real (autorizacion del propietario): contrato completo con evidencia real, no solo-apertura, ofrecido como tarjeta propia, y que
    aun no sea de libre acceso (no habilitado, o habilitado pero con prueba exigida por perfil)."""
    policy = sunat_destinations.policy_of(destination or "")
    return (
        policy is not None and policy.offered and policy.evidence in PROVABLE_EVIDENCE and not policy.open_only
        and (not policy.enabled or policy.requires_profile_proof) and policy.id not in sunat_destinations.EXCLUDED_FROM_SCOPE
    )


def is_profile_proven(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID, destination: str) -> bool:
    """El perfil (de ESTE cliente y ESTE tenant) supero una aceptacion real de ESTE destino: PASS confirmado por el propietario sobre una llegada validada.
    Un FAIL (incluido el del sistema) no prueba nada; la prueba de un perfil/cliente/tenant jamas se extrapola a otro."""
    return db.execute(select(SolDestinationAcceptance.id).where(
        SolDestinationAcceptance.tenant_id == tenant_id, SolDestinationAcceptance.client_id == client_id,
        SolDestinationAcceptance.credential_id == credential_id, SolDestinationAcceptance.destination == destination,
        SolDestinationAcceptance.status == "CONSUMED", SolDestinationAcceptance.result == "PASS",
    ).limit(1)).first() is not None


def proven_destinations(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID) -> set[tuple[uuid.UUID, str]]:
    rows = db.execute(select(SolDestinationAcceptance.credential_id, SolDestinationAcceptance.destination).where(
        SolDestinationAcceptance.tenant_id == tenant_id, SolDestinationAcceptance.client_id == client_id,
        SolDestinationAcceptance.status == "CONSUMED", SolDestinationAcceptance.result == "PASS",
    )).all()
    return {(credential_id, destination) for credential_id, destination in rows}


def access_gate(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID, destination: str) -> str:
    """Compuerta unica de acceso de un destino para UN perfil: libre, con autorizacion de prueba, o inexistente."""
    policy = sunat_destinations.policy_of(sunat_destinations.resolve(destination) or "")
    if policy is None:
        return GATE_DISABLED
    if policy.enabled and not policy.requires_profile_proof:
        return GATE_OPEN
    if policy.enabled and is_profile_proven(db, tenant_id=tenant_id, client_id=client_id, credential_id=credential_id, destination=policy.id):
        return GATE_OPEN
    return GATE_NEEDS_GRANT if is_eligible(policy.id) else GATE_DISABLED


def _row_for_update(db: Session, tenant_id: uuid.UUID, acceptance_id: uuid.UUID) -> SolDestinationAcceptance | None:
    return db.execute(
        select(SolDestinationAcceptance).where(SolDestinationAcceptance.id == acceptance_id, SolDestinationAcceptance.tenant_id == tenant_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _expire_if_due(db: Session, row: SolDestinationAcceptance) -> None:
    if row.status == "AUTHORIZED" and row.expires_at <= clock.db_now(db):
        row.status = "EXPIRED"
        row.version += 1
        db.flush()


# Fases que la UI muestra; NUNCA se deducen en el frontend: las calcula el servidor desde la sesion real.
PHASE_AUTHORIZED = "AUTHORIZED"
PHASE_REVOKED = "REVOKED"
PHASE_EXPIRED = "EXPIRED"
PHASE_RUNNING = "CONSUMED_RUNNING"  # autorizacion consumida, sesion aun en curso
PHASE_FAILED = "SESSION_FAILED"  # la sesion termino SIN llegar al destino (no es aceptable ni confirmable como correcta)
PHASE_AWAITING = "ARRIVAL_AWAITING_CONFIRMATION"  # llegada real validada y entregada al propietario; falta SU confirmacion
PHASE_CONFIRMED_PASS = "CONFIRMED_PASS"
PHASE_CONFIRMED_FAIL = "CONFIRMED_FAIL"

_SESSION_ENDED = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}


def _session_of(db: Session, row: SolDestinationAcceptance) -> SolAccessSession | None:
    if row.session_id is None:
        return None
    return db.execute(select(SolAccessSession).where(SolAccessSession.id == row.session_id, SolAccessSession.tenant_id == row.tenant_id)).scalar_one_or_none()


def _arrival_validated(session: SolAccessSession | None) -> bool:
    """Llegada REAL: el agente valido la pantalla oficial (`DESTINATION_REACHED`) y la entrego al humano (`human_control_at`)."""
    return session is not None and session.operation_result in REACHED_RESULTS and session.human_control_at is not None


def diagnostic_of(db: Session, session: SolAccessSession | None) -> str | None:
    """Codigo de diagnostico estructural (lista cerrada) guardado en la auditoria del cierre de la sesion. Nunca texto del portal."""
    if session is None or session.status != "FAILED":
        return None
    entry = db.execute(
        select(AuditLog).where(AuditLog.tenant_id == session.tenant_id, AuditLog.entity_type == "sol_access_session", AuditLog.entity_id == session.id,
                               AuditLog.action == AuditAction.SESSION_FAILED).order_by(AuditLog.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    code = (entry.log_metadata or {}).get("diagnostic_code") if entry is not None else None
    return code if code in DIAGNOSTIC_CODES else None


def phase_of(db: Session, row: SolDestinationAcceptance) -> tuple[str, SolAccessSession | None]:
    """Distingue autorizacion, consumo, fallo, llegada real y confirmacion humana (y solo asi)."""
    if row.status == "REVOKED":
        return PHASE_REVOKED, None
    if row.status == "EXPIRED":
        return PHASE_EXPIRED, None
    if row.status == "AUTHORIZED":
        return PHASE_AUTHORIZED, None
    session = _session_of(db, row)
    if row.result == "PASS":
        return PHASE_CONFIRMED_PASS, session
    if row.result == "FAIL":
        return PHASE_CONFIRMED_FAIL, session
    if session is None or session.status not in _SESSION_ENDED:
        return PHASE_RUNNING, session
    return (PHASE_AWAITING if _arrival_validated(session) else PHASE_FAILED), session


def _settle_failed_sessions(db: Session, rows: list[SolDestinationAcceptance]) -> None:
    """Una prueba cuya sesion termino sin llegar al destino queda como FAIL del sistema (idempotente): no puede quedar abierta ni confirmarse como correcta."""
    for row in rows:
        if row.status != "CONSUMED" or row.result is not None:
            continue
        phase, session = phase_of(db, row)
        if phase != PHASE_FAILED or session is None:
            continue
        locked = _row_for_update(db, row.tenant_id, row.id)
        if locked is None or locked.result is not None:
            continue
        diagnostic = diagnostic_of(db, session)
        locked.result = "FAIL"
        locked.result_recorded_at = clock.db_now(db)
        locked.result_note = f"SYSTEM:{session.status}" + (f":{session.terminal_reason_code}" if session.terminal_reason_code else "") + (f":{diagnostic}" if diagnostic else "")
        locked.version += 1
        db.flush()
        audit_service.record(
            db, tenant_id=locked.tenant_id, user_id=None, action=AuditAction.SOL_ACCEPTANCE_RESULT_RECORDED, entity_type="sol_destination_acceptance",
            entity_id=locked.id,
            metadata={"client_id": str(locked.client_id), "destination": locked.destination, "result": "FAIL", "source": "SYSTEM", "session_id": str(locked.session_id),
                      "session_status": session.status, "diagnostic_code": diagnostic},
        )


def list_for_client(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID) -> list[SolDestinationAcceptance]:
    rows = list(db.execute(
        select(SolDestinationAcceptance).where(SolDestinationAcceptance.tenant_id == tenant_id, SolDestinationAcceptance.client_id == client_id)
        .order_by(SolDestinationAcceptance.created_at.desc())
    ).scalars())
    for row in rows:
        _expire_if_due(db, row)
    _settle_failed_sessions(db, rows)
    return rows


def grant(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID, destination: str) -> SolDestinationAcceptance:
    if not is_eligible(destination):
        raise AcceptanceError("ACCEPTANCE_DESTINATION_NOT_ELIGIBLE", "solo destinos con contrato completo y prueba pendiente pueden probarse")
    if client_repository.get_by_id(db, tenant_id, client_id) is None:
        raise AcceptanceError("CLIENT_NOT_FOUND")
    if is_profile_proven(db, tenant_id=tenant_id, client_id=client_id, credential_id=credential_id, destination=destination):
        raise AcceptanceError("ACCEPTANCE_ALREADY_PROVEN", "este perfil ya supero la prueba real de este destino")
    credential = db.execute(select(ClientSolCredential).where(
        ClientSolCredential.id == credential_id, ClientSolCredential.tenant_id == tenant_id, ClientSolCredential.client_id == client_id,
    )).scalar_one_or_none()
    if credential is None or credential.status not in USABLE_CREDENTIAL_STATUSES:
        raise AcceptanceError("SOL_CREDENTIAL_NOT_CONFIGURED")  # nunca se sustituye por otro perfil
    now = clock.db_now(db)
    existing = db.execute(select(SolDestinationAcceptance).where(
        SolDestinationAcceptance.tenant_id == tenant_id, SolDestinationAcceptance.client_id == client_id,
        SolDestinationAcceptance.credential_id == credential_id, SolDestinationAcceptance.destination == destination,
        SolDestinationAcceptance.status == "AUTHORIZED",
    ).with_for_update()).scalar_one_or_none()
    if existing is not None:
        if existing.expires_at > now:
            if existing.authorized_by_user_id != user_id:
                raise AcceptanceError("ACCEPTANCE_ALREADY_AUTHORIZED_BY_ANOTHER_USER")
            return existing  # idempotente
        existing.status = "EXPIRED"
        existing.version += 1
        db.flush()
    row = SolDestinationAcceptance(
        tenant_id=tenant_id, client_id=client_id, credential_id=credential_id, destination=destination, authorized_by_user_id=user_id,
        status="AUTHORIZED", created_at=now, expires_at=now + timedelta(seconds=ACCEPTANCE_TTL_SECONDS), version=1,
    )
    db.add(row)
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user_id, action=AuditAction.SOL_ACCEPTANCE_GRANTED, entity_type="sol_destination_acceptance", entity_id=row.id,
        metadata={"client_id": str(client_id), "credential_id": str(credential_id), "credential_profile": credential.profile_name,
                  "destination": destination, "expires_at": row.expires_at.isoformat()},
    )
    return row


def has_valid_grant(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID, destination: str) -> bool:
    if not is_eligible(destination):
        return False
    return db.execute(select(SolDestinationAcceptance.id).where(
        SolDestinationAcceptance.tenant_id == tenant_id, SolDestinationAcceptance.client_id == client_id,
        SolDestinationAcceptance.credential_id == credential_id, SolDestinationAcceptance.destination == destination,
        SolDestinationAcceptance.authorized_by_user_id == user_id, SolDestinationAcceptance.status == "AUTHORIZED",
        SolDestinationAcceptance.expires_at > clock.db_now(db),
    )).first() is not None


def consume(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID, destination: str, session_id: uuid.UUID) -> uuid.UUID:
    """Consumo ATOMICO (fila bloqueada) dentro de la misma transaccion que crea la sesion. Devuelve el id de la autorizacion."""
    if not is_eligible(destination):
        raise AcceptanceError("SOL_DESTINATION_DISABLED")
    row = db.execute(select(SolDestinationAcceptance).where(
        SolDestinationAcceptance.tenant_id == tenant_id, SolDestinationAcceptance.client_id == client_id,
        SolDestinationAcceptance.credential_id == credential_id, SolDestinationAcceptance.destination == destination,
        SolDestinationAcceptance.authorized_by_user_id == user_id, SolDestinationAcceptance.status == "AUTHORIZED",
        SolDestinationAcceptance.expires_at > clock.db_now(db),
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        raise AcceptanceError("SOL_DESTINATION_DISABLED")  # sin autorizacion vigente: mismo rechazo que cualquier destino no validado
    row.status = "CONSUMED"
    row.consumed_at = clock.db_now(db)
    row.session_id = session_id
    row.version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user_id, action=AuditAction.SOL_ACCEPTANCE_CONSUMED, entity_type="sol_destination_acceptance", entity_id=row.id,
        metadata={"client_id": str(client_id), "credential_id": str(credential_id), "destination": destination, "session_id": str(session_id)},
    )
    return row.id


def revoke(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, acceptance_id: uuid.UUID) -> SolDestinationAcceptance:
    row = _row_for_update(db, tenant_id, acceptance_id)
    if row is None:
        raise AcceptanceError("ACCEPTANCE_NOT_FOUND")
    _expire_if_due(db, row)
    if row.status != "AUTHORIZED":
        raise AcceptanceError("ACCEPTANCE_NOT_REVOCABLE")
    row.status = "REVOKED"
    row.version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user_id, action=AuditAction.SOL_ACCEPTANCE_REVOKED, entity_type="sol_destination_acceptance", entity_id=row.id,
        metadata={"client_id": str(row.client_id), "destination": row.destination},
    )
    return row


def record_result(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, acceptance_id: uuid.UUID, result: str, note: str | None) -> SolDestinationAcceptance:
    """El propietario confirma PASS/FAIL tras ver la pantalla oficial. PASS solo si el agente llego realmente al destino (sesion con DESTINATION_REACHED)."""
    if result not in {"PASS", "FAIL"}:
        raise AcceptanceError("ACCEPTANCE_RESULT_INVALID")
    row = _row_for_update(db, tenant_id, acceptance_id)
    if row is None:
        raise AcceptanceError("ACCEPTANCE_NOT_FOUND")
    if row.status != "CONSUMED" or row.session_id is None:
        raise AcceptanceError("ACCEPTANCE_NOT_USED")
    if row.result is not None:
        raise AcceptanceError("ACCEPTANCE_RESULT_ALREADY_RECORDED")
    if row.authorized_by_user_id != user_id:
        raise AcceptanceError("ACCEPTANCE_RESULT_NOT_BY_AUTHORIZER")
    session = _session_of(db, row)
    if session is None or session.status not in _SESSION_ENDED:
        raise AcceptanceError("ACCEPTANCE_SESSION_STILL_RUNNING")  # ni PASS ni FAIL manual mientras la sesion sigue en curso
    if result == "PASS" and not _arrival_validated(session):
        raise AcceptanceError("ACCEPTANCE_PASS_REQUIRES_REACHED_DESTINATION")  # una sesion fallida/cancelada/sin llegada JAMAS se confirma como correcta
    row.result = result
    row.result_recorded_at = clock.db_now(db)
    row.result_note = (note or "").strip()[:NOTE_MAX] or None
    row.version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user_id, action=AuditAction.SOL_ACCEPTANCE_RESULT_RECORDED, entity_type="sol_destination_acceptance", entity_id=row.id,
        metadata={"client_id": str(row.client_id), "destination": row.destination, "result": result, "session_id": str(row.session_id)},
    )
    return row
