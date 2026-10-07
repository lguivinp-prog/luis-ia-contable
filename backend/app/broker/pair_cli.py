"""Fase 5, Bloque 5.2, Domain G -- herramienta de OPERADOR para iniciar un pairing de Agente.

    python -m app.broker.pair_cli --tenant-id <UUID> --user-id <UUID> [--display-name "Mi PC"]

El producto aun no expone un endpoint de usuario para crear pairings (`sol_agent:manage` reservado sin endpoints),
asi que el operador local lo inicia con la MISMA autoridad de servicio que usa el backend (`create_pairing`: mismo
rate limit, mismo verificador HMAC, mismo TTL). El `pairing_id` y el codigo de un solo uso se muestran UNA vez en
esta consola (nunca se persisten en claro ni se escriben en logs); luego se ingresan en `luis-sol-agent.exe pair`."""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User
from app.services import sol_agent_pairing_service as pairing_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.broker.pair_cli", description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--user-id", required=True, type=uuid.UUID, help="Usuario del tenant que solicita el pairing")
    parser.add_argument("--display-name", default="Agente SOL local")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        user = db.execute(
            select(User).where(User.id == args.user_id, User.tenant_id == args.tenant_id, User.is_active.is_(True))
        ).scalar_one_or_none()
        if user is None:
            print("USER_NOT_FOUND_OR_INACTIVE: el usuario no existe en ese tenant o esta inactivo.", file=sys.stderr)
            return 2
        try:
            result = pairing_service.create_pairing(
                db,
                tenant_id=args.tenant_id,
                requested_by_user_id=args.user_id,
                agent_display_name_candidate=args.display_name,
            )
        except pairing_service.SolAgentPairingServiceError as exc:
            db.rollback()
            print(f"PAIRING_NOT_CREATED: {type(exc).__name__}", file=sys.stderr)
            return 3
        pairing_id, code, expires = result.pairing.id, result.pairing_code, result.pairing.expires_at
        db.commit()
    finally:
        db.close()
    print(f"PAIRING_ID={pairing_id}")
    print(f"PAIRING_CODE={code}")
    print(f"EXPIRES_AT={expires.isoformat()}")
    print("Ingrese ambos valores en 'luis-sol-agent.exe pair'. El codigo es de un solo uso y no se vuelve a mostrar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
