import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.obligation import (
    ClientObligationCreate,
    ClientObligationOut,
    ClientObligationUpdate,
    ObligationCatalogOut,
)
from app.services import client_service, obligation_service
from app.services.client_service import ClientNotFoundError
from app.services.obligation_service import (
    ClientObligationNotFoundError,
    DuplicateAssignmentError,
    ObligationNotFoundError,
)

router = APIRouter(prefix="/api/v1", tags=["obligations"])


@router.get("/obligations/catalog", response_model=list[ObligationCatalogOut])
def list_catalog(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.OBLIGATION_READ)),
) -> list[ObligationCatalogOut]:
    return obligation_service.list_catalog(db)


@router.get("/obligations/catalog/{obligation_id}", response_model=ObligationCatalogOut)
def get_catalog_item(
    obligation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.OBLIGATION_READ)),
) -> ObligationCatalogOut:
    try:
        return obligation_service.get_catalog_item(db, obligation_id)
    except ObligationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/clients/{client_id}/obligations", response_model=list[ClientObligationOut])
def list_client_obligations(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.OBLIGATION_READ)),
) -> list[ClientObligationOut]:
    try:
        client_service.get_client(db, current_user.tenant_id, client_id)
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return obligation_service.list_client_obligations(db, current_user.tenant_id, client_id)


@router.post(
    "/clients/{client_id}/obligations",
    response_model=ClientObligationOut,
    status_code=status.HTTP_201_CREATED,
)
def assign_obligation(
    client_id: uuid.UUID,
    payload: ClientObligationCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.OBLIGATION_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> ClientObligationOut:
    try:
        client_service.get_client(db, current_user.tenant_id, client_id)
        client_obligation = obligation_service.assign_obligation(
            db,
            current_user.tenant_id,
            current_user.id,
            client_id,
            payload.obligation_id,
            payload.notes,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(client_obligation)
        return client_obligation
    except ClientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ObligationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DuplicateAssignmentError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch(
    "/clients/{client_id}/obligations/{obligation_id}", response_model=ClientObligationOut
)
def update_client_obligation(
    client_id: uuid.UUID,
    obligation_id: uuid.UUID,
    payload: ClientObligationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.OBLIGATION_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> ClientObligationOut:
    try:
        client_obligation = obligation_service.update_client_obligation(
            db,
            current_user.tenant_id,
            current_user.id,
            client_id,
            obligation_id,
            is_active=payload.is_active,
            notes=payload.notes,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(client_obligation)
        return client_obligation
    except ClientObligationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete(
    "/clients/{client_id}/obligations/{obligation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_client_obligation(
    client_id: uuid.UUID,
    obligation_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.OBLIGATION_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> None:
    try:
        obligation_service.remove_client_obligation(
            db,
            current_user.tenant_id,
            current_user.id,
            client_id,
            obligation_id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
    except ClientObligationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
