"""Fase 3, Bloque 3.2: comunicaciones asistidas por WhatsApp (ver ADR-0067).
Rutas estaticas (`/templates`) antes de las dinamicas (`/drafts/{draft_id}`).
Ningun endpoint envia nada -- el envio real siempre lo hace el usuario, a
mano, desde WhatsApp."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.domain.communication_templates import (
    TemplateNotFoundError,
    UnknownTemplateVariableError,
    list_templates,
)
from app.models.user import User
from app.schemas.communication import (
    CommunicationDraftCreateRequest,
    CommunicationDraftListOut,
    CommunicationDraftOut,
    DiscardResponse,
    ExpectedVersionRequest,
    MarkOpenRequestedResponse,
    MarkSentResponse,
    TemplateOut,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplatesListOut,
)
from app.services import communication_draft_service as svc
from app.services.whatsapp_phone_normalization import (
    InvalidWhatsAppNumberError,
    WhatsAppNumberRequiredError,
)

router = APIRouter(prefix="/api/v1/communications", tags=["communications"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _pagination(limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT), offset: int = Query(0, ge=0)):
    return limit, offset


def _to_draft_out(draft) -> CommunicationDraftOut:
    return CommunicationDraftOut(
        id=draft.id,
        client_id=draft.client_id,
        related_task_id=draft.related_task_id,
        related_document_file_id=draft.related_document_file_id,
        channel=draft.channel,
        template_code=draft.template_code,
        template_version=draft.template_version,
        body=draft.body,
        recipient_phone_display=draft.recipient_phone_display,
        status=draft.status,
        created_by=draft.created_by,
        open_requested_at=draft.open_requested_at,
        marked_sent_at=draft.marked_sent_at,
        marked_sent_by=draft.marked_sent_by,
        discarded_at=draft.discarded_at,
        discarded_by=draft.discarded_by,
        version=draft.version,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


# --- rutas estaticas: catalogo de plantillas ---


@router.get("/templates", response_model=TemplatesListOut)
def get_templates(
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
) -> TemplatesListOut:
    return TemplatesListOut(
        items=[
            TemplateOut(
                code=t.code,
                version=t.version,
                display_name=t.display_name,
                description=t.description,
                variables=list(t.variables),
            )
            for t in list_templates()
        ]
    )


@router.post("/templates/{template_code}/preview", response_model=TemplatePreviewResponse)
def preview_template(
    template_code: str,
    payload: TemplatePreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
    _csrf: None = Depends(verify_csrf),
) -> TemplatePreviewResponse:
    try:
        result = svc.preview_template(
            db,
            tenant_id=current_user.tenant_id,
            actor=current_user,
            template_code=template_code,
            client_id=payload.client_id,
            related_task_id=payload.related_task_id,
            related_document_file_id=payload.related_document_file_id,
            variables=payload.variables,
        )
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    except UnknownTemplateVariableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code}
        ) from exc
    except svc.SourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    except svc.SourceClientMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code}
        ) from exc
    except svc.SourceNotAccessibleError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": exc.code}) from exc
    return TemplatePreviewResponse(
        template_code=result.template_code,
        template_version=result.template_version,
        body_rendered=result.body_rendered,
        recipient_phone_display=result.recipient_phone_display,
        recipient_phone_e164=result.recipient_phone_e164,
        warnings=result.warnings,
    )


# --- borradores ---


@router.post("/drafts", response_model=CommunicationDraftOut)
def create_draft(
    payload: CommunicationDraftCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
    _csrf: None = Depends(verify_csrf),
) -> CommunicationDraftOut:
    try:
        draft = svc.create_draft(
            db,
            tenant_id=current_user.tenant_id,
            actor=current_user,
            client_id=payload.client_id,
            related_task_id=payload.related_task_id,
            related_document_file_id=payload.related_document_file_id,
            template_code=payload.template_code,
            template_version=payload.template_version,
            body=payload.body,
            recipient_phone=payload.recipient_phone,
        )
    except svc.SourceNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    except svc.SourceClientMismatchError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code}
        ) from exc
    except svc.SourceNotAccessibleError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": exc.code}) from exc
    except WhatsAppNumberRequiredError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code}
        ) from exc
    except InvalidWhatsAppNumberError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code}
        ) from exc
    db.commit()
    db.refresh(draft)
    return _to_draft_out(draft)


@router.get("/drafts", response_model=CommunicationDraftListOut)
def get_drafts(
    client_id: uuid.UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    created_by: uuid.UUID | None = Query(None),
    related_task_id: uuid.UUID | None = Query(None),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    pagination: tuple[int, int] = Depends(_pagination),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
) -> CommunicationDraftListOut:
    limit, offset = pagination
    drafts = svc.list_drafts(
        db,
        tenant_id=current_user.tenant_id,
        actor=current_user,
        client_id=client_id,
        status=status_filter,
        created_by=created_by,
        related_task_id=related_task_id,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    total = svc.count_drafts(
        db,
        tenant_id=current_user.tenant_id,
        actor=current_user,
        client_id=client_id,
        status=status_filter,
        created_by=created_by,
        related_task_id=related_task_id,
        created_from=created_from,
        created_to=created_to,
    )
    return CommunicationDraftListOut(
        items=[_to_draft_out(d) for d in drafts], total=total, limit=limit, offset=offset
    )


@router.get("/drafts/{draft_id}", response_model=CommunicationDraftOut)
def get_draft(
    draft_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
) -> CommunicationDraftOut:
    try:
        draft = svc.get_draft(
            db, tenant_id=current_user.tenant_id, actor=current_user, draft_id=draft_id
        )
    except svc.DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    except svc.DraftNotOwnedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": exc.code}) from exc
    return _to_draft_out(draft)


@router.post("/drafts/{draft_id}/mark-open-requested", response_model=MarkOpenRequestedResponse)
def mark_open_requested(
    draft_id: uuid.UUID,
    payload: ExpectedVersionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
    _csrf: None = Depends(verify_csrf),
) -> MarkOpenRequestedResponse:
    try:
        draft, wa_url = svc.mark_open_requested(
            db,
            tenant_id=current_user.tenant_id,
            actor=current_user,
            draft_id=draft_id,
            expected_version=payload.expected_version,
        )
    except svc.DraftNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    except svc.DraftNotOwnedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": exc.code}) from exc
    except svc.InvalidCommunicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    except svc.VersionConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    db.commit()
    db.refresh(draft)
    return MarkOpenRequestedResponse(
        draft=_to_draft_out(draft), wa_url=wa_url, message="Se solicitó abrir WhatsApp"
    )


@router.post("/drafts/{draft_id}/mark-sent", response_model=MarkSentResponse)
def mark_sent(
    draft_id: uuid.UUID,
    payload: ExpectedVersionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
    _csrf: None = Depends(verify_csrf),
) -> MarkSentResponse:
    try:
        draft = svc.mark_sent(
            db,
            tenant_id=current_user.tenant_id,
            actor=current_user,
            draft_id=draft_id,
            expected_version=payload.expected_version,
        )
    except svc.DraftNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    except svc.DraftNotOwnedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": exc.code}) from exc
    except svc.InvalidCommunicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    except svc.VersionConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    db.commit()
    db.refresh(draft)
    return MarkSentResponse(draft=_to_draft_out(draft), message="Marcado como enviado por ti")


@router.post("/drafts/{draft_id}/discard", response_model=DiscardResponse)
def discard_draft(
    draft_id: uuid.UUID,
    payload: ExpectedVersionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMMUNICATION_DRAFT)),
    _csrf: None = Depends(verify_csrf),
) -> DiscardResponse:
    try:
        draft = svc.discard_draft(
            db,
            tenant_id=current_user.tenant_id,
            actor=current_user,
            draft_id=draft_id,
            expected_version=payload.expected_version,
        )
    except svc.DraftNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": exc.code}) from exc
    except svc.DraftNotOwnedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": exc.code}) from exc
    except svc.InvalidCommunicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    except svc.VersionConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    db.commit()
    db.refresh(draft)
    return DiscardResponse(draft=_to_draft_out(draft), message="Borrador descartado")
