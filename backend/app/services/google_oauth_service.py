"""Fundamento de integraciones Google (Fase 3 / Bloque 3.1, ver ADR-0062,
ADR-0063, ADR-0065).

IMPORTANTE -- excepcion documentada a ADR-0037 (ver ADR-0065): a diferencia
de todo otro servicio de dominio del repositorio, `process_callback` maneja
sus propias transacciones cortas (`db.commit()`), en vez de dejar que el
router controle una unica transaccion para todo el endpoint. Esto es
deliberado: el intercambio de codigo con Google es una llamada HTTP externa
lenta que nunca debe ejecutarse mientras se sostiene abierta una transaccion
de escritura sobre `google_oauth_authorization_attempts`/
`google_oauth_connections`. El router de este bloque NO debe volver a
hacer `commit()` despues de llamar a este servicio -- este servicio ya deja
la base de datos en el estado final correcto por si mismo.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.integration_crypto import (
    EncryptionContext,
    IntegrationDecryptionError,
    IntegrationKeyMissingError,
    decrypt_secret,
    encrypt_secret,
    is_encryption_configured,
)
from app.domain.google_oauth.provider import (
    GoogleIdentity,
    OAuthProvider,
    OAuthProviderError,
    OAuthTokenExchangeError,
    OAuthTokenResult,
    OAuthUserInfoError,
)
from app.models.audit_log import AuditAction
from app.models.google_oauth import (
    GoogleOAuthAttemptErrorCode,
    GoogleOAuthAttemptStatus,
    GoogleOAuthAuthorizationAttempt,
    GoogleOAuthCapability,
    GoogleOAuthConnection,
    GoogleOAuthConnectionStatus,
    GoogleOAuthDisconnectionOrigin,
)
from app.repositories import google_oauth_repository as repo
from app.services import audit_service

ATTEMPT_TTL_SECONDS = 600  # 10 minutos (Seccion 8 del plan v3)

# Scopes minimos por capacidad (Seccion 9 del plan v3). El Bloque 3.1 solo
# habilita IDENTITY -- CALENDAR/DRIVE se agregaran en bloques posteriores.
SCOPES_BY_CAPABILITY: dict[str, tuple[str, ...]] = {
    GoogleOAuthCapability.IDENTITY.value: ("openid", "email"),
}

# Capacidades que este bloque realmente admite. Ampliar esto SOLO cuando el
# bloque correspondiente (3.2/3.3) este implementado -- nunca antes.
ENABLED_CAPABILITIES: frozenset[str] = frozenset({GoogleOAuthCapability.IDENTITY.value})


class GoogleOAuthServiceError(Exception):
    """Error generico normalizado, expuesto por el router como un codigo estable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IntegrationMisconfiguredError(GoogleOAuthServiceError):
    def __init__(self) -> None:
        super().__init__("INTEGRATION_MISCONFIGURED", "La integracion con Google no esta configurada")


class InvalidStateError(GoogleOAuthServiceError):
    def __init__(self) -> None:
        super().__init__("INVALID_STATE", "El intento de autorizacion no es valido")


class CapabilityNotEnabledError(GoogleOAuthServiceError):
    def __init__(self, capability: str) -> None:
        super().__init__("CAPABILITY_NOT_AUTHORIZED", f"La capacidad '{capability}' no esta habilitada")


class ConnectionNotFoundError(GoogleOAuthServiceError):
    def __init__(self) -> None:
        super().__init__("CONNECTION_NOT_FOUND", "No existe una conexion Google para este usuario")


@dataclass(frozen=True)
class CallbackOutcome:
    succeeded: bool
    error_code: str | None
    connection: GoogleOAuthConnection | None


def _generate_pkce_pair() -> tuple[str, str]:
    """Genera (code_verifier, code_challenge) segun RFC 7636 -- verifier
    aleatorio url-safe (43-128 caracteres), challenge = base64url(SHA-256(verifier))
    sin padding."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _require_encryption_configured() -> None:
    if not is_encryption_configured():
        raise IntegrationMisconfiguredError()


def is_google_oauth_configured() -> bool:
    """Google esta "configurado" (GOOGLE_LIVE_VALIDATION_DEFERRED, ver
    ADR-0063) unicamente cuando existen credenciales OAuth reales -- nunca
    segun `GOOGLE_OAUTH_PROVIDER`, que es solo el selector de adaptador para
    desarrollo/pruebas. Mientras client_id/client_secret esten vacios, el
    resto del sistema debe seguir funcionando con normalidad; esta funcion
    nunca abre una conexion de red ni lanza una excepcion."""
    settings = get_settings()
    return bool(settings.google_oauth_client_id) and bool(settings.google_oauth_client_secret)


def start_authorization(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    capability: str,
    return_path: str,
    redirect_uri: str,
    oauth_provider: OAuthProvider,
) -> tuple[str, uuid.UUID]:
    """Crea un intento PENDING y devuelve (authorization_url, attempt_id).
    Operacion de una sola fase -- no hay llamada externa aqui, el router
    controla la transaccion normalmente (sin excepcion a ADR-0037)."""
    _require_encryption_configured()
    if capability not in ENABLED_CAPABILITIES:
        raise CapabilityNotEnabledError(capability)

    scopes = list(SCOPES_BY_CAPABILITY[capability])
    existing_connection = repo.get_connection(db, tenant_id, user_id)
    # prompt=consent siempre en la primera conexion o mientras no exista un
    # refresh_token utilizable (NEEDS_REAUTH/DISCONNECTED) -- ver Seccion 9.
    prompt_consent = existing_connection is None or existing_connection.status in (
        GoogleOAuthConnectionStatus.NEEDS_REAUTH.value,
        GoogleOAuthConnectionStatus.DISCONNECTED.value,
    )

    state = secrets.token_urlsafe(32)
    state_hash = _hash_state(state)
    code_verifier, code_challenge = _generate_pkce_pair()

    # El id se genera aqui, no lo asigna la BD, porque el AAD del cifrado del
    # code_verifier (Seccion 12.1 del plan v3) necesita el id de la fila --
    # y la fila nunca puede insertarse con code_verifier_encrypted=NULL
    # mientras status='PENDING' (violaria ck_google_oauth_attempts_status_
    # coherence). Todo el INSERT ocurre una sola vez, ya completo.
    attempt_id = uuid.uuid4()
    envelope, key_id = encrypt_secret(
        code_verifier,
        EncryptionContext(
            table_name="google_oauth_authorization_attempts",
            row_id=str(attempt_id),
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            token_type="PKCE_VERIFIER",
        ),
    )

    now = clock.utcnow()
    attempt = GoogleOAuthAuthorizationAttempt(
        id=attempt_id,
        tenant_id=tenant_id,
        user_id=user_id,
        state_hash=state_hash,
        code_verifier_encrypted=envelope,
        credential_key_id=key_id,
        requested_capability=capability,
        requested_scopes=scopes,
        return_path=return_path,
        status=GoogleOAuthAttemptStatus.PENDING.value,
        expires_at=now + timedelta(seconds=ATTEMPT_TTL_SECONDS),
        created_at=now,
    )
    db.add(attempt)
    db.flush()

    authorization_url = oauth_provider.build_authorization_url(
        state=state,
        code_challenge=code_challenge,
        scopes=scopes,
        redirect_uri=redirect_uri,
        prompt_consent=prompt_consent,
    )
    return authorization_url, attempt.id


def _fail_attempt(
    db: Session,
    attempt: GoogleOAuthAuthorizationAttempt,
    *,
    error_code: str,
    consumed_at_now: bool,
) -> None:
    now = clock.utcnow()
    attempt.code_verifier_encrypted = None
    attempt.credential_key_id = None
    attempt.status = GoogleOAuthAttemptStatus.FAILED.value
    attempt.error_code = error_code
    attempt.completed_at = now
    if consumed_at_now:
        attempt.consumed_at = now
    db.flush()
    audit_service.record(
        db,
        tenant_id=attempt.tenant_id,
        user_id=attempt.user_id,
        action=AuditAction.INTEGRATION_GOOGLE_CONNECT_FAILED,
        entity_type="google_oauth_authorization_attempt",
        entity_id=attempt.id,
        metadata={"error_code": error_code, "capability": attempt.requested_capability},
    )
    db.commit()


def process_callback(
    db: Session,
    *,
    current_tenant_id: uuid.UUID,
    current_user_id: uuid.UUID,
    state: str,
    code: str | None,
    error: str | None,
    redirect_uri: str,
    oauth_provider: OAuthProvider,
) -> CallbackOutcome:
    """Flujo de 12 pasos del callback OAuth (Seccion 8 del plan v3, corregido
    en la ronda de implementacion con los casos access_denied/expirado).
    Excepcion documentada a ADR-0037: usa transacciones cortas propias."""
    state_hash = _hash_state(state)
    attempt = repo.lock_attempt_by_state_hash(db, state_hash)

    if attempt is None:
        db.rollback()
        raise InvalidStateError()

    if (
        attempt.status != GoogleOAuthAttemptStatus.PENDING.value
        or attempt.tenant_id != current_tenant_id
        or attempt.user_id != current_user_id
    ):
        # Segundo callback con el mismo state, o un state que no pertenece a
        # la sesion actual -- nunca se escribe nada, nunca se llama a Google.
        db.rollback()
        raise InvalidStateError()

    now = clock.utcnow()
    if attempt.expires_at <= now:
        _fail_attempt(
            db, attempt, error_code=GoogleOAuthAttemptErrorCode.STATE_EXPIRED.value, consumed_at_now=True
        )
        return CallbackOutcome(
            succeeded=False, error_code=GoogleOAuthAttemptErrorCode.STATE_EXPIRED.value, connection=None
        )

    if error is not None:
        _fail_attempt(
            db,
            attempt,
            error_code=GoogleOAuthAttemptErrorCode.AUTHORIZATION_DENIED.value,
            consumed_at_now=True,
        )
        return CallbackOutcome(
            succeeded=False,
            error_code=GoogleOAuthAttemptErrorCode.AUTHORIZATION_DENIED.value,
            connection=None,
        )

    # --- descifrar el verifier EN MEMORIA, antes de purgarlo ---
    try:
        code_verifier = decrypt_secret(
            attempt.code_verifier_encrypted,
            attempt.credential_key_id,
            EncryptionContext(
                table_name="google_oauth_authorization_attempts",
                row_id=str(attempt.id),
                tenant_id=str(attempt.tenant_id),
                user_id=str(attempt.user_id),
                token_type="PKCE_VERIFIER",
            ),
        )
    except (IntegrationDecryptionError, IntegrationKeyMissingError):
        _fail_attempt(
            db, attempt, error_code=GoogleOAuthAttemptErrorCode.UNRECOVERABLE_ERROR.value,
            consumed_at_now=True,
        )
        return CallbackOutcome(
            succeeded=False, error_code=GoogleOAuthAttemptErrorCode.UNRECOVERABLE_ERROR.value, connection=None
        )

    # --- PENDING -> PROCESSING: purgar verifier, fijar consumed_at, commit corto ---
    attempt.status = GoogleOAuthAttemptStatus.PROCESSING.value
    attempt.code_verifier_encrypted = None
    attempt.credential_key_id = None
    attempt.consumed_at = now
    db.flush()
    db.commit()

    # --- fuera de toda transaccion: intercambio de codigo + userinfo ---
    assert code is not None  # invariante: sin error, Google siempre envia code
    try:
        token_result = oauth_provider.exchange_code(
            code=code, code_verifier=code_verifier, redirect_uri=redirect_uri
        )
        identity = oauth_provider.get_user_info(access_token=token_result.access_token)
    except OAuthTokenExchangeError:
        attempt2 = repo.lock_attempt_by_id(db, current_tenant_id, attempt.id)
        _fail_attempt(
            db, attempt2, error_code=GoogleOAuthAttemptErrorCode.TOKEN_EXCHANGE_FAILED.value,
            consumed_at_now=False,
        )
        return CallbackOutcome(
            succeeded=False,
            error_code=GoogleOAuthAttemptErrorCode.TOKEN_EXCHANGE_FAILED.value,
            connection=None,
        )
    except OAuthUserInfoError:
        attempt2 = repo.lock_attempt_by_id(db, current_tenant_id, attempt.id)
        _fail_attempt(
            db, attempt2, error_code=GoogleOAuthAttemptErrorCode.USERINFO_FAILED.value, consumed_at_now=False
        )
        return CallbackOutcome(
            succeeded=False, error_code=GoogleOAuthAttemptErrorCode.USERINFO_FAILED.value, connection=None
        )

    # Defensa en profundidad: aunque GoogleOAuthProvider ya valida esto en el
    # adaptador real, el servicio nunca confia ciegamente en un `sub`/`email`
    # vacio proveniente de cualquier implementacion de OAuthProvider (ver
    # Seccion 8 del plan v3 -- "exigir sub/email no vacios").
    if not identity.sub or not identity.email:
        attempt2 = repo.lock_attempt_by_id(db, current_tenant_id, attempt.id)
        _fail_attempt(
            db, attempt2, error_code=GoogleOAuthAttemptErrorCode.USERINFO_FAILED.value,
            consumed_at_now=False,
        )
        return CallbackOutcome(
            succeeded=False, error_code=GoogleOAuthAttemptErrorCode.USERINFO_FAILED.value, connection=None
        )

    if not identity.email_verified:
        attempt2 = repo.lock_attempt_by_id(db, current_tenant_id, attempt.id)
        _fail_attempt(
            db, attempt2, error_code=GoogleOAuthAttemptErrorCode.INVALID_EMAIL_NOT_VERIFIED.value,
            consumed_at_now=False,
        )
        return CallbackOutcome(
            succeeded=False,
            error_code=GoogleOAuthAttemptErrorCode.INVALID_EMAIL_NOT_VERIFIED.value,
            connection=None,
        )

    # --- nueva transaccion: validar cuenta + persistir conexion ---
    attempt2 = repo.lock_attempt_by_id(db, current_tenant_id, attempt.id)
    existing_connection = repo.lock_connection_for_update(db, current_tenant_id, current_user_id)

    if not token_result.refresh_token:
        has_usable_refresh_token = (
            existing_connection is not None and existing_connection.refresh_token_encrypted is not None
        )
        if not has_usable_refresh_token:
            _fail_attempt(
                db, attempt2, error_code=GoogleOAuthAttemptErrorCode.REFRESH_TOKEN_REQUIRED.value,
                consumed_at_now=False,
            )
            return CallbackOutcome(
                succeeded=False,
                error_code=GoogleOAuthAttemptErrorCode.REFRESH_TOKEN_REQUIRED.value,
                connection=None,
            )

    existing_is_connected = (
        existing_connection is not None
        and existing_connection.status != GoogleOAuthConnectionStatus.DISCONNECTED.value
    )
    if existing_is_connected:
        if existing_connection.google_account_id != identity.sub:
            _fail_attempt(
                db, attempt2, error_code=GoogleOAuthAttemptErrorCode.GOOGLE_ACCOUNT_MISMATCH.value,
                consumed_at_now=False,
            )
            return CallbackOutcome(
                succeeded=False,
                error_code=GoogleOAuthAttemptErrorCode.GOOGLE_ACCOUNT_MISMATCH.value,
                connection=None,
            )

    try:
        connection, was_account_replacement = _apply_successful_authorization(
            db,
            tenant_id=current_tenant_id,
            user_id=current_user_id,
            existing_connection=existing_connection,
            identity=identity,
            token_result=token_result,
            capability=attempt2.requested_capability,
        )
    except IntegrityError:
        # Otra transaccion concurrente ya conecto esta misma cuenta Google
        # a otro usuario del tenant (UNIQUE(tenant_id, google_account_id))
        # entre la comprobacion de mismatch y este INSERT/UPDATE -- nunca se
        # propaga un IntegrityError crudo hacia el router (ver Seccion 21).
        db.rollback()
        attempt3 = repo.lock_attempt_by_id(db, current_tenant_id, attempt.id)
        _fail_attempt(
            db,
            attempt3,
            error_code=GoogleOAuthAttemptErrorCode.ACCOUNT_ALREADY_CONNECTED.value,
            consumed_at_now=False,
        )
        return CallbackOutcome(
            succeeded=False,
            error_code=GoogleOAuthAttemptErrorCode.ACCOUNT_ALREADY_CONNECTED.value,
            connection=None,
        )

    attempt2.status = GoogleOAuthAttemptStatus.SUCCEEDED.value
    attempt2.completed_at = clock.utcnow()
    db.flush()
    audit_service.record(
        db,
        tenant_id=current_tenant_id,
        user_id=current_user_id,
        action=(
            AuditAction.INTEGRATION_GOOGLE_ACCOUNT_REPLACED
            if was_account_replacement
            else AuditAction.INTEGRATION_GOOGLE_CONNECT
        ),
        entity_type="google_oauth_connection",
        entity_id=connection.id,
        metadata={"capability": attempt2.requested_capability},
    )
    db.commit()

    return CallbackOutcome(succeeded=True, error_code=None, connection=connection)


def _apply_successful_authorization(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    existing_connection: GoogleOAuthConnection | None,
    identity: GoogleIdentity,
    token_result: OAuthTokenResult,
    capability: str,
) -> tuple[GoogleOAuthConnection, bool]:
    now = clock.utcnow()
    is_account_replacement = (
        existing_connection is not None
        and existing_connection.status == GoogleOAuthConnectionStatus.DISCONNECTED.value
        and existing_connection.google_account_id != identity.sub
    )

    if existing_connection is None:
        # No se hace flush todavia -- una fila ACTIVE recien creada sin
        # tokens violaria ck_google_oauth_connections_status_coherence. El
        # id se genera aqui (no lo asigna la BD) para poder usarlo en el AAD
        # del cifrado de tokens mas abajo; el unico INSERT real ocurre en el
        # flush final de esta funcion, con la fila ya completa.
        connection = GoogleOAuthConnection(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            provider="google",
            google_account_email=identity.email,
            google_account_id=identity.sub,
            scopes_granted=[],
            status=GoogleOAuthConnectionStatus.ACTIVE.value,
            first_connected_at=now,
            authorized_at=now,
            version=1,
        )
        db.add(connection)
    else:
        connection = existing_connection
        if is_account_replacement:
            connection.google_account_id = identity.sub
            connection.google_account_email = identity.email
            connection.scopes_granted = []
        else:
            connection.google_account_email = identity.email

    # Regla unica de scopes (Seccion 9 del plan v3): si la respuesta trae una
    # lista de scopes, se almacena exactamente esa (Google ya devuelve el
    # acumulado real con include_granted_scopes=true); si no trae ninguna
    # (p. ej. un refresco de token), se conservan los scopes_granted previos.
    if token_result.scopes_granted is not None:
        connection.scopes_granted = list(token_result.scopes_granted)
    else:
        connection.scopes_granted = list(connection.scopes_granted or [])

    # Regla unica de refresh_token (Seccion 9 del plan v3): si Google no
    # devuelve uno nuevo, se conserva el cifrado existente sin tocarlo --
    # nunca se reemplaza por NULL. Si devuelve uno nuevo, se cifra y
    # reemplaza (cubre tambien el reemplazo de cuenta tras DISCONNECTED,
    # donde por construccion siempre llega un refresh_token nuevo).
    if token_result.refresh_token is not None:
        envelope, key_id = encrypt_secret(
            token_result.refresh_token,
            EncryptionContext(
                table_name="google_oauth_connections",
                row_id=str(connection.id),
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                token_type="REFRESH",
            ),
        )
        connection.refresh_token_encrypted = envelope
        connection.token_key_id = key_id

    access_envelope, access_key_id = encrypt_secret(
        token_result.access_token,
        EncryptionContext(
            table_name="google_oauth_connections",
            row_id=str(connection.id),
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            token_type="ACCESS",
        ),
    )
    connection.access_token_encrypted = access_envelope
    connection.token_key_id = access_key_id
    connection.access_token_expires_at = token_result.access_token_expires_at
    connection.status = GoogleOAuthConnectionStatus.ACTIVE.value
    connection.authorized_at = now
    connection.reauth_required_at = None
    connection.reauth_reason = None
    connection.disconnected_at = None
    connection.disconnected_by = None
    connection.disconnection_origin = None
    connection.version += 1 if existing_connection is not None else 0
    db.flush()
    return connection, is_account_replacement


def get_status(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    configured = is_google_oauth_configured()
    connection = repo.get_connection(db, tenant_id, user_id)
    if connection is None:
        return {
            "configured": configured,
            "connected": False,
            "status": None,
            "google_account_email": None,
            "identity_authorized": False,
            "calendar_authorized": False,
            "drive_authorized": False,
            "authorized_at": None,
            "reauth_reason": None,
        }
    scopes = set(connection.scopes_granted or [])
    return {
        "configured": configured,
        "connected": connection.status != GoogleOAuthConnectionStatus.DISCONNECTED.value,
        "status": connection.status,
        "google_account_email": connection.google_account_email,
        "identity_authorized": set(SCOPES_BY_CAPABILITY[GoogleOAuthCapability.IDENTITY.value]).issubset(
            scopes
        ),
        "calendar_authorized": False,  # capacidad aun no habilitada en este bloque
        "drive_authorized": False,
        "authorized_at": connection.authorized_at,
        "reauth_reason": connection.reauth_reason,
    }


def disconnect(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    oauth_provider: OAuthProvider,
) -> tuple[GoogleOAuthConnection, bool]:
    """Desconexion manual. La revocacion remota (mejor esfuerzo) ocurre
    ANTES de bloquear la fila para escritura -- nunca se sostiene un bloqueo
    de fila mientras se espera la llamada HTTP a Google."""
    connection = repo.get_connection(db, tenant_id, user_id)
    if connection is None or connection.status == GoogleOAuthConnectionStatus.DISCONNECTED.value:
        raise ConnectionNotFoundError()

    revocation_succeeded = True
    if connection.access_token_encrypted and connection.token_key_id:
        try:
            access_token = decrypt_secret(
                connection.access_token_encrypted,
                connection.token_key_id,
                EncryptionContext(
                    table_name="google_oauth_connections",
                    row_id=str(connection.id),
                    tenant_id=str(tenant_id),
                    user_id=str(user_id),
                    token_type="ACCESS",
                ),
            )
            oauth_provider.revoke_token(token=access_token)
        except (IntegrationDecryptionError, OAuthProviderError):
            revocation_succeeded = False

    locked = repo.lock_connection_for_update(db, tenant_id, user_id)
    now = clock.utcnow()
    locked.status = GoogleOAuthConnectionStatus.DISCONNECTED.value
    locked.access_token_encrypted = None
    locked.refresh_token_encrypted = None
    locked.token_key_id = None
    locked.access_token_expires_at = None
    locked.reauth_required_at = None
    locked.reauth_reason = None
    locked.disconnected_at = now
    locked.disconnected_by = actor_user_id
    locked.disconnection_origin = GoogleOAuthDisconnectionOrigin.MANUAL.value
    locked.version += 1
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=AuditAction.INTEGRATION_GOOGLE_DISCONNECT,
        entity_type="google_oauth_connection",
        entity_id=locked.id,
        metadata={"revocation_succeeded": revocation_succeeded, "origin": "MANUAL"},
    )
    return locked, revocation_succeeded


def disconnect_for_user_deactivation(
    db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Se invoca desde `user_service.deactivate_user`. NUNCA realiza una
    llamada HTTP a Google -- solo limpia tokens locales y marca DISCONNECTED/
    SYSTEM (ver Seccion 7 del plan v3). El servicio llamador controla la
    transaccion (no hace commit aqui)."""
    connection = repo.lock_connection_for_update(db, tenant_id, user_id)
    if connection is None or connection.status == GoogleOAuthConnectionStatus.DISCONNECTED.value:
        return
    now = clock.utcnow()
    connection.status = GoogleOAuthConnectionStatus.DISCONNECTED.value
    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.token_key_id = None
    connection.access_token_expires_at = None
    connection.reauth_required_at = None
    connection.reauth_reason = None
    connection.disconnected_at = now
    connection.disconnected_by = None
    connection.disconnection_origin = GoogleOAuthDisconnectionOrigin.SYSTEM.value
    connection.version += 1
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.INTEGRATION_GOOGLE_DISCONNECT,
        entity_type="google_oauth_connection",
        entity_id=connection.id,
        metadata={"revocation_succeeded": None, "origin": "SYSTEM", "reason": "USER_DEACTIVATED"},
    )
