from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.file_storage import FileStorageBackend, LocalFileStorageBackend
from app.db.session import get_db
from app.domain.google_oauth.fake_provider import FakeOAuthProvider
from app.domain.google_oauth.google_provider import GoogleOAuthProvider
from app.domain.google_oauth.provider import OAuthProvider
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


# Instancia unica de proceso: a diferencia de FakeRucLookupProvider (puro,
# sin estado), FakeOAuthProvider guarda codigos/tokens en memoria entre la
# peticion que los registra (setup de prueba) y la peticion que los consume
# (callback) -- debe ser el mismo objeto en ambas, nunca una instancia nueva
# por peticion.
_fake_oauth_provider_singleton: FakeOAuthProvider | None = None


def get_fake_oauth_provider_singleton() -> FakeOAuthProvider:
    global _fake_oauth_provider_singleton
    if _fake_oauth_provider_singleton is None:
        _fake_oauth_provider_singleton = FakeOAuthProvider()
    return _fake_oauth_provider_singleton


def reset_fake_oauth_provider_singleton() -> None:
    """Solo para aislamiento entre pruebas."""
    global _fake_oauth_provider_singleton
    _fake_oauth_provider_singleton = None


def get_oauth_provider() -> OAuthProvider:
    # El adaptador real nunca se selecciona sin credenciales reales, aunque
    # GOOGLE_OAUTH_PROVIDER="google" quede mal configurado (ver
    # GOOGLE_LIVE_VALIDATION_DEFERRED, ADR-0063): sin client_id/client_secret
    # cualquier llamada real fallaria de todas formas, asi que se evita
    # construir ese adaptador desde el origen.
    if (
        settings.google_oauth_provider == "google"
        and settings.google_oauth_client_id
        and settings.google_oauth_client_secret
    ):
        return GoogleOAuthProvider()
    return get_fake_oauth_provider_singleton()


def get_file_storage() -> FileStorageBackend:
    """Fabrica de dependencia FastAPI para el backend de almacenamiento de
    archivos documentales (Bloque 5, ADR-0053). Instancia liviana (solo
    resuelve la raiz configurada) -- se crea una por peticion, sin estado
    compartido entre requests."""
    return LocalFileStorageBackend(settings.document_storage_root)


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
