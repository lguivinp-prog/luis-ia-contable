from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.ruc_lookup.fake_provider import FakeRucLookupProvider
from app.domain.ruc_lookup.provider import RucLookupProvider
from app.domain.ruc_lookup.sunat_provider import SunatRucLookupProvider
from app.models.session import Session as SessionModel
from app.models.user import User
from app.repositories import user_repository
from app.services import auth_service
from app.services.csrf_service import verify_csrf_token

settings = get_settings()

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_ruc_lookup_provider() -> RucLookupProvider:
    if settings.ruc_lookup_provider == "sunat":
        return SunatRucLookupProvider()
    return FakeRucLookupProvider()


def get_current_session(request: Request, db: Session = Depends(get_db)) -> SessionModel:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")

    session = auth_service.get_valid_session(db, raw_token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")

    db.commit()
    return session


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> User:
    user = user_repository.get_by_id(db, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado o inactivo"
        )
    return user


def verify_csrf(
    request: Request,
    session: SessionModel = Depends(get_current_session),
) -> None:
    """Verifica el token CSRF (double-submit cookie, ver ADR-0010) para
    metodos mutables. No depende de ningun middleware del frontend: esta
    dependencia es la unica que protege la API."""
    if request.method in _SAFE_METHODS:
        return

    provided = request.headers.get("x-csrf-token")
    if not verify_csrf_token(provided, session.csrf_token_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token CSRF inválido")


def get_client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def ensure_admin_role(user: User) -> None:
    """Verificacion de rol de una sola posicion (ver "Cierre operativo
    completo" del plan): el RBAC real queda para una fase futura -- por
    ahora solo distingue `admin` del resto para gatear el cierre con
    pendientes (`force_close_with_pending`)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta accion requiere rol de administrador",
        )
