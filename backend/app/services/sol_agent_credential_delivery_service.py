"""Entrega segura de credencial SOL a un agente Windows emparejado (Fase 5,
Bloque 5.2, Domain E1-B -- ver
docs/phase5/block5_2/BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md, congelado en
Domain E1-A/E1-A.1). Dos operaciones cohesivas y deliberadamente separadas:

- `authorize_credential_delivery`: invocada por un actor humano ya
  autenticado (futura User API; en E1-B, pruebas directas) -- NUNCA
  descifra, solo autoriza una ventana de oportunidad de un solo uso.
- `deliver_credential`: invocada por el broker (canal mTLS operacional) tras
  autenticar al agente -- descifra JUST-IN-TIME y consume el gate de
  entrega de Domain C. Nunca reimplementa `mark_credential_delivered`, que
  sigue siendo la unica autoridad transaccional.

Ningun secreto (username/password/RUC en claro, `vault_root_key`,
`credential_dek`) sale jamas de este modulo hacia un registro process-local
-- ver `app.broker.secret_delivery_grant.SecretDeliveryGrant`, que solo
contiene metadata no-secreta."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.broker.secret_delivery_grant import SecretDeliveryGrantRegistry
from app.core import clock
from app.core import sol_vault_crypto as crypto
from app.core.permissions import Permission, has_permission
from app.core.sol_vault_registry import (
    VaultLockedError,
    VaultOperationNotAuthorizedError,
    VaultUnlockExpiredError,
    VaultUnlockRegistry,
)
from app.models.audit_log import AuditAction
from app.models.sol_agent_access import SESSION_TERMINAL_STATUSES
from app.models.user import User
from app.repositories import client_repository
from app.repositories import sol_agent_access_repository as agent_repo
from app.repositories import sol_vault_repository as vault_repo
from app.services import audit_service
from app.services import sol_agent_job_service as job_service

_CREDENTIAL_DELIVERY_OPERATION = "CREDENTIAL_DELIVERY"


class SolAgentCredentialDeliveryError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class CredentialAuthorizationNotEligibleError(SolAgentCredentialDeliveryError):
    """Generico y deliberadamente indistinguible (mismo principio que
    `JobClaimInvalidError` en Domain C): sesion/job inexistente, sesion
    terminal/vencida, job de otra sesion, job no `CLAIMED`, agente no
    `ACTIVE`, credencial inexistente/de otro cliente, o gate ya
    consumido."""

    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_AUTHORIZATION_NOT_ELIGIBLE")


class CredentialAuthorizationForbiddenError(SolAgentCredentialDeliveryError):
    """El actor no es exactamente `requested_by_user_id` de la sesion, o
    no tiene AMBOS permisos `sol_session:create` + `sol_credential:read`
    (seccion 2.1/8 del documento de arquitectura)."""

    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_AUTHORIZATION_FORBIDDEN")


class CredentialAuthorizationVaultLockedError(SolAgentCredentialDeliveryError):
    """La vault no esta actualmente desbloqueada (`CREDENTIAL_DELIVERY`)
    para el `(tenant, vault, actor, app_session)` exacto -- crear un
    grant mientras la vault esta bloqueada esta expresamente prohibido
    (seccion 19 del encargo: "Vault must be unlocked at authorization")."""

    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_AUTHORIZATION_VAULT_LOCKED")


class CredentialDeliveryJobNotEligibleError(SolAgentCredentialDeliveryError):
    """Generico (mismo principio no-oracle): job inexistente, de otro
    agente, no `CLAIMED`, vencido, o sesion terminal/vencida -- nunca
    distingue el motivo especifico ante el llamador de transporte."""

    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_DELIVERY_JOB_NOT_ELIGIBLE")


class CredentialDeliveryUnavailableError(SolAgentCredentialDeliveryError):
    """Generico: grant ausente/vencido/ya `IN_FLIGHT`/de otro agente,
    vault bloqueada al momento de la entrega, fallo de descifrado, o
    rechazo del gate de entrega de Domain C."""

    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_DELIVERY_UNAVAILABLE")


@dataclass(frozen=True)
class DeliveryMaterial:
    """Contenedor efimero PURAMENTE en memoria de proceso -- nunca
    almacenado en un registro, nunca cacheado, nunca logueado. `__repr__`
    redactado por la misma razon que `CredentialDeliveryResponse`
    (`app.broker.protocol`)."""

    ruc: str
    sol_username: str
    sol_password: str

    def __repr__(self) -> str:
        return "DeliveryMaterial(ruc='***REDACTED***', sol_username='***REDACTED***', sol_password='***REDACTED***')"

    def __str__(self) -> str:
        return self.__repr__()


def authorize_credential_delivery(
    session_factory: sessionmaker,
    *,
    grant_registry: SecretDeliveryGrantRegistry,
    vault_registry: VaultUnlockRegistry,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    job_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
) -> None:
    """Autoriza una ventana de oportunidad de un solo uso -- NUNCA
    descifra, nunca devuelve texto plano.

    EXCEPCION DOCUMENTADA A ADR-0037 (mismo patron ya productivo que el
    `db.rollback()` de `confirm_pairing` ante `IntegrityError`, Domain C,
    y que las transacciones cortas propias de
    `google_oauth_service.process_callback`, ADR-0065): esta funcion SI
    ejecuta `db.commit()` explicitamente. Domain E1-A/E1-A.1 (seccion
    20/21 del encargo de Domain E1-B) exige que el registro DURABLE de
    auditoria (`CREDENTIAL_DELIVERY_AUTHORIZED`) quede comprometido en
    PostgreSQL ANTES de que el grant process-local se active -- un
    `db.flush()` sin commit, dejando el commit al llamador, abriria una
    ventana donde el grant ya esta disponible pero la auditoria todavia
    podria perderse en un rollback posterior no relacionado
    (`AUDIT_FAILURE_CAN_LEAVE_ACTIVE_GRANT=NO`).

    Domain E1-B.1, Gate A (correccion de cierre): a diferencia de
    `process_callback` -- que puede aceptar con seguridad una `Session`
    externa porque FastAPI le garantiza una sesion fresca por-peticion,
    dedicada de antemano por el router -- esta funcion NO tiene todavia
    un router productivo que le de esa garantia (E1-B: "en E1-B, pruebas
    directas"). Aceptar una `Session` arbitraria del llamador y luego
    hacer `db.commit()` sobre ella comprometeria CUALQUIER mutacion no
    relacionada que el llamador ya tuviera pendiente en esa misma sesion
    -- un commit compartido nunca puede ser parcial en SQLAlchemy/
    PostgreSQL. La unica forma de garantizar
    `AUTHORIZATION_COMMIT_CAN_COMMIT_UNRELATED_CALLER_STATE=NO` mientras
    se preserva la durabilidad real del commit (una savepoint/transaccion
    anidada NO basta: su "commit" solo libera el savepoint, no persiste
    nada en disco hasta el commit EXTERNO, lo cual violaria
    `AUDIT_COMMITTED_BEFORE_GRANT_AVAILABLE=YES`) es que esta funcion
    ABRA Y POSEA su propia sesion dedicada, en su propia conexion, desde
    `session_factory()` hasta `close()` -- nunca compartida con nada mas.
    Mismo patron ya productivo de `session_factory` usado por
    `BrokerServer`/`app/broker/server.py`, no una arquitectura nueva."""
    db = session_factory()
    try:
        now_db = clock.db_now(db)

        session_row = agent_repo.get_session(db, tenant_id=tenant_id, session_id=session_id)
        if session_row is None:
            raise CredentialAuthorizationNotEligibleError()
        if session_row.status in SESSION_TERMINAL_STATUSES or session_row.expires_at <= now_db:
            raise CredentialAuthorizationNotEligibleError()
        if session_row.requested_by_user_id != actor.id:
            raise CredentialAuthorizationForbiddenError()
        if not (
            has_permission(actor, Permission.SOL_SESSION_CREATE)
            and has_permission(actor, Permission.SOL_CREDENTIAL_READ)
        ):
            raise CredentialAuthorizationForbiddenError()

        job = agent_repo.get_job(db, tenant_id=tenant_id, job_id=job_id)
        if job is None or job.session_id != session_id:
            raise CredentialAuthorizationNotEligibleError()
        if job.status != "CLAIMED":
            raise CredentialAuthorizationNotEligibleError()
        if job.credential_delivered_at is not None:
            raise CredentialAuthorizationNotEligibleError()

        agent = agent_repo.get_agent(db, tenant_id=tenant_id, agent_id=job.agent_id)
        if agent is None or agent.status != "ACTIVE":
            raise CredentialAuthorizationNotEligibleError()

        credential = vault_repo.get_credential_by_id(
            db, tenant_id=tenant_id, client_id=session_row.client_id, credential_id=session_row.credential_id
        )
        if credential is None:
            raise CredentialAuthorizationNotEligibleError()

        # Re-verificacion en vivo de que la vault esta desbloqueada para
        # este actor/sesion de aplicacion exactos, con
        # operation=CREDENTIAL_DELIVERY -- nunca VERIFY_ACCESS (seccion
        # 4/6 del encargo). Prueba viva de desbloqueo al momento de
        # AUTORIZAR (seccion 19); la entrega vuelve a re-verificar por su
        # cuenta (seccion 22), este chequeo nunca sustituye a ese segundo.
        try:
            vault_registry.resolve(
                db,
                tenant_id=tenant_id,
                vault_id=credential.vault_id,
                user_id=actor.id,
                app_session_id=app_session_id,
                operation=_CREDENTIAL_DELIVERY_OPERATION,
                now=clock.utcnow(),
            )
        except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError):
            raise CredentialAuthorizationVaultLockedError() from None

        audit_service.record(
            db,
            tenant_id=tenant_id,
            user_id=actor.id,
            action=AuditAction.CREDENTIAL_DELIVERY_AUTHORIZED,
            entity_type="sol_agent_job",
            entity_id=job_id,
            metadata={"session_id": str(session_id)},
        )
        db.commit()

        grant_registry.authorize(
            tenant_id=tenant_id,
            session_id=session_id,
            job_id=job_id,
            agent_id=job.agent_id,
            user_id=actor.id,
            app_session_id=app_session_id,
            credential_id=credential.id,
        )
    finally:
        db.close()


def _decrypt_credential(
    db: Session, *, tenant_id: uuid.UUID, credential, vault_root_key: bytes
) -> tuple[str, str]:
    """Punto de descifrado UNICO y nombrado (Domain E1-B, seccion 51 del
    encargo: `DECRYPT_CALL_COUNT` debe poder medirse espiando esta funcion
    exacta, mismo patron que `app.broker.pki._perform_ca_signature` en
    Domain D.1). Resuelve la version de `credential_dek` REALMENTE usada
    al cifrar esta credencial (`credential.data_key_version`) -- nunca
    asume la version `ACTIVE` actual, que pudo haber rotado desde
    entonces (seccion 67 del encargo)."""
    data_key_row = vault_repo.get_data_key_by_version(
        db, tenant_id=tenant_id, vault_id=credential.vault_id, data_key_version=credential.data_key_version
    )
    aad_data_key = crypto.aad_wrapped_data_key(
        data_key_row_id=data_key_row.id,
        tenant_id=tenant_id,
        vault_id=credential.vault_id,
        data_key_version=data_key_row.data_key_version,
    )
    credential_dek = crypto.unwrap_data_key(data_key_row.wrapped_data_key, vault_root_key, aad_data_key)

    username_aad = crypto.aad_credential_field(
        credential_id=credential.id,
        tenant_id=tenant_id,
        client_id=credential.client_id,
        field_name="sol_username",
        data_key_version=credential.data_key_version,
    )
    password_aad = crypto.aad_credential_field(
        credential_id=credential.id,
        tenant_id=tenant_id,
        client_id=credential.client_id,
        field_name="sol_password",
        data_key_version=credential.data_key_version,
    )
    sol_username = crypto.decrypt_credential_field(credential.sol_username_encrypted, credential_dek, username_aad)
    sol_password = crypto.decrypt_credential_field(credential.sol_password_encrypted, credential_dek, password_aad)
    return sol_username, sol_password


def deliver_credential(
    db: Session,
    *,
    grant_registry: SecretDeliveryGrantRegistry,
    vault_registry: VaultUnlockRegistry,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    job_id: uuid.UUID,
) -> DeliveryMaterial:
    """Pasos 4-11 del orden exacto congelado (`identity` viene SOLO de
    `agent_id`, ya derivado de mTLS por el llamador -- nunca de un campo
    de protocolo). El paso 12 (commit) y los pasos 13 en adelante
    (revalidacion final, consumo del grant, escritura de red) son
    responsabilidad exclusiva del llamador (`app.broker.server`), que es
    quien controla el ciclo de vida completo de la transaccion HTTP/DB
    para esta peticion -- esta funcion NUNCA hace commit (seguiria
    ADR-0037 de no ser por la excepcion ya documentada y acotada de
    `authorize_credential_delivery`, que es una funcion DISTINTA)."""
    now_db = clock.db_now(db)

    job = agent_repo.get_job(db, tenant_id=tenant_id, job_id=job_id)
    if job is None or job.agent_id != agent_id:
        raise CredentialDeliveryJobNotEligibleError()
    if job.status != "CLAIMED":
        raise CredentialDeliveryJobNotEligibleError()
    if job.expires_at <= now_db:
        raise CredentialDeliveryJobNotEligibleError()

    session_row = agent_repo.get_session(db, tenant_id=tenant_id, session_id=job.session_id)
    if session_row is None or session_row.status in SESSION_TERMINAL_STATUSES or session_row.expires_at <= now_db:
        raise CredentialDeliveryJobNotEligibleError()

    credential = vault_repo.get_credential_by_id(
        db, tenant_id=tenant_id, client_id=session_row.client_id, credential_id=session_row.credential_id
    )
    if credential is None:
        raise CredentialDeliveryJobNotEligibleError()

    client = client_repository.get_by_id(db, tenant_id, session_row.client_id)
    if client is None:
        raise CredentialDeliveryJobNotEligibleError()

    grant = grant_registry.try_acquire_in_flight(tenant_id=tenant_id, job_id=job_id, agent_id=agent_id)
    if grant is None:
        raise CredentialDeliveryUnavailableError()

    try:
        entry = vault_registry.resolve(
            db,
            tenant_id=tenant_id,
            vault_id=credential.vault_id,
            user_id=grant.user_id,
            app_session_id=grant.app_session_id,
            operation=_CREDENTIAL_DELIVERY_OPERATION,
            now=clock.utcnow(),
        )
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError):
        grant_registry.invalidate(tenant_id=tenant_id, job_id=job_id)
        raise CredentialDeliveryUnavailableError() from None

    try:
        sol_username, sol_password = _decrypt_credential(
            db, tenant_id=tenant_id, credential=credential, vault_root_key=bytes(entry.vault_root_key)
        )
    except crypto.SolVaultDecryptionError:
        grant_registry.invalidate(tenant_id=tenant_id, job_id=job_id)
        raise CredentialDeliveryUnavailableError() from None

    try:
        job_service.mark_credential_delivered(
            db, tenant_id=tenant_id, job_id=job_id, expected_version=job.version
        )
    except job_service.SolAgentJobServiceError:
        grant_registry.invalidate(tenant_id=tenant_id, job_id=job_id)
        raise CredentialDeliveryUnavailableError() from None

    return DeliveryMaterial(ruc=client.ruc, sol_username=sol_username, sol_password=sol_password)
