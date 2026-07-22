"""Desbloqueo administrativo local de un identificador de login (ver ADR-0009).

No borra historial: inserta un registro de exito sintetico, que hace que el
calculo de fallos consecutivos (contados desde el ultimo exito) vuelva a
cero. Uso: python -m app.cli.unlock_login correo@ejemplo.com
"""

import argparse
import sys

from app.core import clock
from app.core.identifier import InvalidIdentifierError, normalize_login_identifier
from app.db.session import SessionLocal
from app.models.security_event import SecurityEventSeverity, SecurityEventType
from app.repositories import login_attempt_repository, security_event_repository, user_repository


def unlock(email: str) -> None:
    try:
        normalized = normalize_login_identifier(email)
    except InvalidIdentifierError as exc:
        print(f"[unlock-login] Identificador invalido: {exc}", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        user = user_repository.get_by_email(db, normalized)
        tenant_id = user.tenant_id if user else None
        now = clock.utcnow()

        login_attempt_repository.record(
            db,
            tenant_id=tenant_id,
            identifier=normalized,
            success=True,
            attempted_at=now,
            ip_address=None,
            user_agent="cli:unlock_login",
        )
        security_event_repository.create(
            db,
            tenant_id=tenant_id,
            user_id=user.id if user else None,
            event_type=SecurityEventType.LOGIN_UNLOCKED_MANUALLY.value,
            severity=SecurityEventSeverity.INFO.value,
            identifier=normalized,
            metadata=None,
            ip_address=None,
            user_agent="cli:unlock_login",
            created_at=now,
        )
        db.commit()
        print(f"[unlock-login] Identificador '{normalized}' desbloqueado. Historial conservado.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Desbloqueo administrativo local de un identificador de login"
    )
    parser.add_argument("email", help="Correo/identificador a desbloquear")
    args = parser.parse_args()
    unlock(args.email)


if __name__ == "__main__":
    main()
