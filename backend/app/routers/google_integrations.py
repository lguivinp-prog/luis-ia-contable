"""Fase 3, Bloque 3.1: fundamento de integraciones y conexion OAuth Google
de identidad (ver ADR-0062/ADR-0063/ADR-0065). 4 endpoints: status,
authorize, callback, disconnect."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import get_oauth_provider, verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.domain.google_oauth.provider import OAuthProvider
from app.models.user import User
from app.schemas.google_oauth import (
    GoogleAuthorizeRequest,
    GoogleAuthorizeResponse,
    GoogleConnectionStatusOut,
    GoogleDisconnectResponse,
)
from app.services import google_oauth_service
from app.services.google_oauth_service import (
    CapabilityNotEnabledError,
    ConnectionNotFoundError,
    IntegrationMisconfiguredError,
    InvalidStateError,
)

router = APIRouter(prefix="/api/v1/integrations/google", tags=["integrations-google"])


def _redirect_uri() -> str:
    settings = get_settings()
    return f"{settings.app_public_base_url}/api/v1/integrations/google/callback"


@router.get("/status", response_model=GoogleConnectionStatusOut)
def get_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.INTEGRATION_CONNECT)),
) -> GoogleConnectionStatusOut:
    status_dict = google_oauth_service.get_status(
        db, tenant_id=current_user.tenant_id, user_id=current_user.id
    )
    return GoogleConnectionStatusOut(**status_dict)


@router.post("/authorize", response_model=GoogleAuthorizeResponse)
def authorize(
    payload: GoogleAuthorizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.INTEGRATION_CONNECT)),
    oauth_provider: OAuthProvider = Depends(get_oauth_provider),
    _csrf: None = Depends(verify_csrf),
) -> GoogleAuthorizeResponse:
    try:
        authorization_url, attempt_id = google_oauth_service.start_authorization(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            capability=payload.capability,
            return_path=payload.return_path,
            redirect_uri=_redirect_uri(),
            oauth_provider=oauth_provider,
        )
    except IntegrationMisconfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    except CapabilityNotEnabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code}
        ) from exc
    db.commit()
    return GoogleAuthorizeResponse(authorization_url=authorization_url, attempt_id=attempt_id)


@router.get("/callback")
def callback(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.INTEGRATION_CONNECT)),
    oauth_provider: OAuthProvider = Depends(get_oauth_provider),
) -> RedirectResponse:
    # No se registra request.url ni request.query_params en ningun log de
    # esta funcion (ver Seccion 15 del plan v3: code/state nunca en logs).
    state = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    settings = get_settings()

    if not state:
        return RedirectResponse(
            url=f"{settings.frontend_public_base_url}/settings/integrations/google"
            f"?google_error=INVALID_STATE",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        outcome = google_oauth_service.process_callback(
            db,
            current_tenant_id=current_user.tenant_id,
            current_user_id=current_user.id,
            state=state,
            code=code,
            error=error,
            redirect_uri=_redirect_uri(),
            oauth_provider=oauth_provider,
        )
    except InvalidStateError:
        # state inexistente, ya consumido, o de otro tenant/usuario -- nunca
        # se escribio nada (ver Seccion 8 del plan v3). Nunca se propaga como
        # 500: el navegador debe recibir la misma redireccion con error que
        # cualquier otro fallo del callback.
        query = urlencode({"google_error": "INVALID_STATE"})
        return RedirectResponse(
            url=f"{settings.frontend_public_base_url}/settings/integrations/google?{query}",
            status_code=status.HTTP_302_FOUND,
        )
    # process_callback ya maneja sus propias transacciones cortas (excepcion
    # documentada a ADR-0037, ver ADR-0065) -- este router NUNCA vuelve a
    # llamar db.commit() aqui.
    if outcome.succeeded:
        query = urlencode({"google_status": "connected"})
    else:
        query = urlencode({"google_error": outcome.error_code or "UNRECOVERABLE_ERROR"})
    return RedirectResponse(
        url=f"{settings.frontend_public_base_url}/settings/integrations/google?{query}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/disconnect", response_model=GoogleDisconnectResponse)
def disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.INTEGRATION_DISCONNECT)),
    oauth_provider: OAuthProvider = Depends(get_oauth_provider),
    _csrf: None = Depends(verify_csrf),
) -> GoogleDisconnectResponse:
    try:
        _connection, revocation_succeeded = google_oauth_service.disconnect(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            actor_user_id=current_user.id,
            oauth_provider=oauth_provider,
        )
    except ConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    db.commit()
    return GoogleDisconnectResponse(disconnected=True, revocation_succeeded=revocation_succeeded)
