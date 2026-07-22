from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.ruc_lookup.fake_provider import FakeRucLookupProvider
from app.domain.ruc_lookup.provider import RucLookupProvider
from app.domain.ruc_lookup.sunat_provider import SunatRucLookupProvider
from app.models.user import User
from app.repositories import user_repository
from app.services import auth_service

settings = get_settings()


def get_ruc_lookup_provider() -> RucLookupProvider:
    if settings.ruc_lookup_provider == "sunat":
        return SunatRucLookupProvider()
    return FakeRucLookupProvider()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")

    session = auth_service.get_valid_session(db, raw_token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    user = user_repository.get_by_id(db, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado o inactivo"
        )

    db.commit()
    return user


def get_client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
