import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, get_current_user, get_ruc_lookup_provider, verify_csrf
from app.db.session import get_db
from app.domain.ruc_lookup.provider import RucLookupProvider
from app.models.user import User
from app.schemas.client import (
    ClientCreate,
    ClientListOut,
    ClientOut,
    ClientStatusUpdate,
    ClientUpdate,
    RucLookupOut,
)
from app.services import client_service
from app.services.client_service import ClientNotFoundError, DuplicateRucError
from app.services.ruc_validator import InvalidRucError, is_valid_ruc

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])


@router.get("", response_model=ClientListOut)
def list_clients(
    service_status: str | None = None,
    tax_regime: str | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    items, total = client_service.list_clients(
        db,
        current_user.tenant_id,
        service_status=service_status,
        tax_regime=tax_regime,
        search=search,
        limit=limit,
        offset=offset,
    )
    out_items = client_service.to_client_list_out(db, current_user.tenant_id, items)
    return {"items": out_items, "total": total, "limit": limit, "offset": offset}


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(
    payload: ClientCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ClientOut:
    try:
        client = client_service.create_client(
            db,
            current_user.tenant_id,
            current_user.id,
            payload,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(client)
        return client_service.to_client_out(db, current_user.tenant_id, client)
    except InvalidRucError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except DuplicateRucError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/validate-ruc/{ruc}")
def validate_ruc_format(
    ruc: str, current_user: User = Depends(get_current_user)
) -> dict:
    return {"ruc": ruc, "is_valid": is_valid_ruc(ruc)}


@router.get("/ruc-lookup/{ruc}", response_model=RucLookupOut)
def ruc_lookup(
    ruc: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    provider: RucLookupProvider = Depends(get_ruc_lookup_provider),
) -> RucLookupOut:
    try:
        result = client_service.lookup_ruc(
            db,
            current_user.tenant_id,
            current_user.id,
            ruc,
            provider,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        return RucLookupOut(**result.__dict__)
    except InvalidRucError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except NotImplementedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc


@router.get("/{client_id}", response_model=ClientOut)
def get_client(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClientOut:
    try:
        client = client_service.get_client(db, current_user.tenant_id, client_id)
        return client_service.to_client_out(db, current_user.tenant_id, client)
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{client_id}", response_model=ClientOut)
def update_client(
    client_id: uuid.UUID,
    payload: ClientUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ClientOut:
    try:
        client = client_service.update_client(
            db,
            current_user.tenant_id,
            current_user.id,
            client_id,
            payload,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(client)
        return client_service.to_client_out(db, current_user.tenant_id, client)
    except ClientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidRucError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except DuplicateRucError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/{client_id}/status", response_model=ClientOut)
def change_client_status(
    client_id: uuid.UUID,
    payload: ClientStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ClientOut:
    try:
        client = client_service.change_status(
            db,
            current_user.tenant_id,
            current_user.id,
            client_id,
            payload.service_status.value,
            payload.note,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(client)
        return client_service.to_client_out(db, current_user.tenant_id, client)
    except ClientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
