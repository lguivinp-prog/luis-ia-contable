"""Gestion de usuarios (Bloque 4, ADR-0051). Ningun metodo de este servicio
ejecuta `commit()`/`rollback()`: usa `flush()` unicamente; el router controla
la transaccion (mismo principio que el resto del backend, ver ADR-0037).
Protege la invariante "cada tenant conserva siempre al menos un usuario activo
con role='admin'" mediante bloqueo determinista (`FOR UPDATE`, ver
`user_repository.lock_target_and_active_admins`) + un predicado puro,
unitariamente testeable sin base de datos."""

import uuid

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.user import User
from app.repositories import user_repository
from app.services import auth_service, google_oauth_service


class UserNotFoundError(Exception):
    pass


class DuplicateEmailError(Exception):
    pass


class SelfOperationNotAllowedError(Exception):
    pass


class NoRoleChangeError(Exception):
    pass


class LastActiveAdminError(Exception):
    pass


class UserAlreadyActiveError(Exception):
    pass


def removes_last_active_admin(
    target: User, admins: list[User], *, new_role: str | None, deactivating: bool
) -> bool:
    """True si aplicar la operacion sobre `target` dejaria al tenant sin
    ningun admin activo. `admins` ya viene bajo `SELECT ... FOR UPDATE`
    (ver `user_repository.lock_target_and_active_admins`) -- funcion pura,
    sin acceso a la base de datos, para poder probarse unitariamente."""
    target_is_active_admin = target.is_active and target.role == "admin"
    if not target_is_active_admin:
        return False  # el objetivo no cuenta hoy como admin activo; nada que proteger
    operation_removes_admin_role_or_activity = deactivating or (
        new_role is not None and new_role != "admin"
    )
    if not operation_removes_admin_role_or_activity:
        return False
    return len(admins) <= 1  # target es el unico admin activo bajo bloqueo


def list_users(db: Session, tenant_id: uuid.UUID) -> list[User]:
    return user_repository.list_by_tenant(db, tenant_id)


def create_user(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    email: str,
    full_name: str,
    role: str,
    password: str,
) -> User:
    if user_repository.get_by_email(db, email.strip().lower()) is not None:
        raise DuplicateEmailError(f"Ya existe un usuario con el correo {email}")

    user = User(
        tenant_id=tenant_id,
        email=email.strip().lower(),
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    return user


def change_role(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    target_id: uuid.UUID,
    *,
    new_role: str,
    reason: str,
) -> tuple[User, str]:
    """Retorna (usuario_actualizado, rol_anterior) para que el router pueda
    auditar `from_role`/`to_role`."""
    if target_id == actor.id:
        raise SelfOperationNotAllowedError("Un usuario no puede cambiar su propio rol")

    target, admins = user_repository.lock_target_and_active_admins(db, tenant_id, target_id)
    if target is None:
        raise UserNotFoundError("Usuario no encontrado")

    if new_role == target.role:
        raise NoRoleChangeError("El nuevo rol es igual al actual")

    if removes_last_active_admin(target, admins, new_role=new_role, deactivating=False):
        raise LastActiveAdminError(
            "No se puede cambiar el rol: el tenant se quedaría sin ningún admin activo"
        )

    previous_role = target.role
    target.role = new_role
    db.flush()
    return target, previous_role


def deactivate_user(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    target_id: uuid.UUID,
    *,
    now,
    reason: str,
) -> User:
    if target_id == actor.id:
        raise SelfOperationNotAllowedError("Un usuario no puede desactivarse a sí mismo")

    target, admins = user_repository.lock_target_and_active_admins(db, tenant_id, target_id)
    if target is None:
        raise UserNotFoundError("Usuario no encontrado")

    if removes_last_active_admin(target, admins, new_role=None, deactivating=True):
        raise LastActiveAdminError(
            "No se puede desactivar: el tenant se quedaría sin ningún admin activo"
        )

    target.is_active = False
    target.deactivated_at = now
    target.deactivated_by = actor.id
    target.deactivation_reason = reason
    auth_service.revoke_all_sessions(db, target.id)
    # Fase 3 / Bloque 3.1: limpia localmente cualquier conexion Google del
    # usuario desactivado -- nunca llama a Google (ver Seccion 7 del plan v3).
    google_oauth_service.disconnect_for_user_deactivation(db, tenant_id=tenant_id, user_id=target_id)
    db.flush()
    return target


def reactivate_user(db: Session, tenant_id: uuid.UUID, actor: User, target_id: uuid.UUID) -> User:
    if target_id == actor.id:
        raise SelfOperationNotAllowedError("Un usuario no puede reactivarse a sí mismo")

    target = user_repository.get_by_id_and_tenant(db, tenant_id, target_id)
    if target is None:
        raise UserNotFoundError("Usuario no encontrado")
    if target.is_active:
        raise UserAlreadyActiveError("El usuario ya está activo")

    target.is_active = True
    target.deactivated_at = None
    target.deactivated_by = None
    target.deactivation_reason = None
    db.flush()
    return target


def reset_password(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    target_id: uuid.UUID,
    *,
    new_password: str,
    reason: str,
) -> User:
    if target_id == actor.id:
        raise SelfOperationNotAllowedError(
            "Usa el cambio de contraseña propio (/auth/change-password) en vez de esto"
        )

    target = user_repository.get_by_id_and_tenant(db, tenant_id, target_id)
    if target is None:
        raise UserNotFoundError("Usuario no encontrado")

    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    auth_service.revoke_all_sessions(db, target.id)
    db.flush()
    return target
