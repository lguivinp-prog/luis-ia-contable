"""Comunicaciones asistidas por WhatsApp (Fase 3 / Bloque 3.2, ver
ADR-0067). El envio real del mensaje SIEMPRE lo hace el usuario, a mano,
desde la aplicacion de WhatsApp -- este servicio nunca envia nada, nunca usa
WhatsApp Business API ni WhatsApp Cloud API, nunca automatiza ni scrapea
WhatsApp Web. `mark_open_requested` solo registra que el usuario solicito
abrir el enlace `wa.me`; `mark_sent` es exclusivamente una declaracion
humana autodeclarada -- ninguno de los dos confirma entrega, lectura ni
recepcion por parte de WhatsApp."""

from __future__ import annotations

import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import clock
from app.domain.communication_templates import (
    TemplateNotFoundError,
    UnknownTemplateVariableError,
    get_template,
)
from app.models.audit_log import AuditAction
from app.models.client import Client
from app.models.communication_draft import CommunicationDraft, CommunicationDraftStatus
from app.models.declaration_document_file import DeclarationDocumentFile
from app.models.operational_task import OperationalTask
from app.models.user import User
from app.repositories import communication_draft_repository as repo
from app.services import audit_service
from app.services.whatsapp_phone_normalization import (
    InvalidWhatsAppNumberError,
    WhatsAppNumberRequiredError,
    normalize_whatsapp_number,
)

__all__ = [
    "TemplateNotFoundError",
    "UnknownTemplateVariableError",
    "WhatsAppNumberRequiredError",
    "InvalidWhatsAppNumberError",
    "CommunicationServiceError",
    "DraftNotFoundError",
    "DraftNotOwnedError",
    "SourceClientMismatchError",
    "SourceNotFoundError",
    "SourceNotAccessibleError",
    "VersionConflictError",
    "InvalidCommunicationTransitionError",
    "PreviewResult",
    "preview_template",
    "create_draft",
    "list_drafts",
    "count_drafts",
    "get_draft",
    "mark_open_requested",
    "mark_sent",
    "discard_draft",
]

_ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    CommunicationDraftStatus.DRAFT.value: (
        CommunicationDraftStatus.OPEN_REQUESTED.value,
        CommunicationDraftStatus.DISCARDED.value,
    ),
    CommunicationDraftStatus.OPEN_REQUESTED.value: (
        CommunicationDraftStatus.OPEN_REQUESTED.value,  # idempotente, ver mark_open_requested
        CommunicationDraftStatus.MARKED_SENT.value,
        CommunicationDraftStatus.DISCARDED.value,
    ),
}


class CommunicationServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DraftNotFoundError(CommunicationServiceError):
    def __init__(self) -> None:
        super().__init__("DRAFT_NOT_FOUND", "El borrador no existe en este tenant")


class DraftNotOwnedError(CommunicationServiceError):
    def __init__(self) -> None:
        super().__init__(
            "DRAFT_NOT_OWNED", "El rol asistente solo puede operar borradores creados por si mismo"
        )


class SourceClientMismatchError(CommunicationServiceError):
    def __init__(self, source: str) -> None:
        super().__init__(
            "SOURCE_CLIENT_MISMATCH", f"{source} no pertenece al cliente seleccionado"
        )


class SourceNotFoundError(CommunicationServiceError):
    def __init__(self, source: str) -> None:
        super().__init__("SOURCE_NOT_FOUND", f"{source} no existe en este tenant")


class SourceNotAccessibleError(CommunicationServiceError):
    def __init__(self, source: str) -> None:
        super().__init__("SOURCE_NOT_ACCESSIBLE", f"{source} no es accesible para este usuario")


class VersionConflictError(CommunicationServiceError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            "VERSION_CONFLICT", f"version desactualizada: esperada {expected}, actual {actual}"
        )


class InvalidCommunicationTransitionError(CommunicationServiceError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            "INVALID_COMMUNICATION_TRANSITION", f"transicion invalida: {current} -> {target}"
        )


@dataclass(frozen=True)
class PreviewResult:
    template_code: str
    template_version: int
    body_rendered: str
    recipient_phone_display: str
    recipient_phone_e164: str
    warnings: list[str]


def _short_phone_for_audit(e164: str) -> str:
    """Nunca el numero completo en auditoria/logs -- como maximo los
    ultimos 4 digitos (Seccion 12 del pedido)."""
    return f"***{e164[-4:]}" if len(e164) >= 4 else "***"


def _resolve_client(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.tenant_id != tenant_id:
        raise SourceNotFoundError("client_id")
    return client


def _validate_related_task(
    db: Session,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    actor: User,
    related_task_id: uuid.UUID | None,
) -> None:
    if related_task_id is None:
        return
    task = db.get(OperationalTask, related_task_id)
    if task is None or task.tenant_id != tenant_id:
        raise SourceNotFoundError("related_task_id")
    if task.client_id != client_id:
        raise SourceClientMismatchError("related_task_id")
    if actor.role == "asistente" and task.assigned_to != actor.id:
        raise SourceNotAccessibleError("related_task_id")


def _validate_related_document(
    db: Session,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    related_document_file_id: uuid.UUID | None,
) -> None:
    if related_document_file_id is None:
        return
    document = db.get(DeclarationDocumentFile, related_document_file_id)
    if document is None or document.tenant_id != tenant_id:
        raise SourceNotFoundError("related_document_file_id")
    if document.client_id != client_id:
        raise SourceClientMismatchError("related_document_file_id")


def preview_template(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor: User,
    template_code: str,
    client_id: uuid.UUID,
    related_task_id: uuid.UUID | None,
    related_document_file_id: uuid.UUID | None,
    variables: dict[str, str],
) -> PreviewResult:
    """No persiste nada -- solo renderiza. Las mismas validaciones de
    fuente que `create_draft` (mismo tenant/cliente, ownership de
    asistente), para que la vista previa nunca revele informacion de una
    tarea/documento de otro cliente."""
    template = get_template(template_code)
    client = _resolve_client(db, tenant_id, client_id)
    _validate_related_task(db, tenant_id, client_id, actor, related_task_id)
    _validate_related_document(db, tenant_id, client_id, related_document_file_id)

    body_rendered = template.render(variables)

    warnings: list[str] = []
    try:
        display, e164 = normalize_whatsapp_number(client.whatsapp_number)
    except WhatsAppNumberRequiredError:
        display, e164 = "", ""
        warnings.append("WHATSAPP_NUMBER_REQUIRED")
    except InvalidWhatsAppNumberError:
        display, e164 = client.whatsapp_number or "", ""
        warnings.append("INVALID_WHATSAPP_NUMBER")

    return PreviewResult(
        template_code=template.code,
        template_version=template.version,
        body_rendered=body_rendered,
        recipient_phone_display=display,
        recipient_phone_e164=e164,
        warnings=warnings,
    )


def create_draft(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor: User,
    client_id: uuid.UUID,
    related_task_id: uuid.UUID | None,
    related_document_file_id: uuid.UUID | None,
    template_code: str | None,
    template_version: int | None,
    body: str,
    recipient_phone: str | None,
) -> CommunicationDraft:
    client = _resolve_client(db, tenant_id, client_id)
    _validate_related_task(db, tenant_id, client_id, actor, related_task_id)
    _validate_related_document(db, tenant_id, client_id, related_document_file_id)

    phone_source = recipient_phone if recipient_phone else client.whatsapp_number
    display, e164 = normalize_whatsapp_number(phone_source)

    now = clock.utcnow()
    draft = CommunicationDraft(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        client_id=client_id,
        related_task_id=related_task_id,
        related_document_file_id=related_document_file_id,
        channel="WHATSAPP",
        template_code=template_code,
        template_version=template_version,
        body=body,
        recipient_phone_display=display,
        recipient_phone_e164=e164,
        status=CommunicationDraftStatus.DRAFT.value,
        created_by=actor.id,
        version=1,
        created_at=now,
        updated_at=now,
    )
    repo.create(db, draft)

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.COMMUNICATION_DRAFT_CREATE,
        entity_type="communication_draft",
        entity_id=draft.id,
        metadata={
            "client_id": str(client_id),
            "template_code": template_code,
            "new_status": draft.status,
            "new_version": draft.version,
            "related_task_id": str(related_task_id) if related_task_id else None,
            "related_document_file_id": (
                str(related_document_file_id) if related_document_file_id else None
            ),
            "recipient_phone_suffix": _short_phone_for_audit(e164),
        },
    )
    return draft


def _ensure_asistente_owns_draft(actor: User, draft: CommunicationDraft) -> None:
    if actor.role != "asistente":
        return
    if draft.created_by != actor.id:
        raise DraftNotOwnedError()


def get_draft(
    db: Session, *, tenant_id: uuid.UUID, actor: User, draft_id: uuid.UUID
) -> CommunicationDraft:
    draft = repo.get_by_id(db, tenant_id, draft_id)
    if draft is None:
        raise DraftNotFoundError()
    _ensure_asistente_owns_draft(actor, draft)
    return draft


def list_drafts(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor: User,
    client_id: uuid.UUID | None = None,
    status: str | None = None,
    created_by: uuid.UUID | None = None,
    related_task_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CommunicationDraft]:
    """Ownership real aplicado en el backend (nunca solo en el frontend):
    un asistente SIEMPRE queda restringido a sus propios borradores, sin
    importar el `created_by` que intente pasar como filtro."""
    owner_restriction = actor.id if actor.role == "asistente" else None
    return repo.list_drafts(
        db,
        tenant_id,
        client_id=client_id,
        status=status,
        created_by=created_by,
        related_task_id=related_task_id,
        owner_restriction=owner_restriction,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )


def count_drafts(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor: User,
    client_id: uuid.UUID | None = None,
    status: str | None = None,
    created_by: uuid.UUID | None = None,
    related_task_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> int:
    owner_restriction = actor.id if actor.role == "asistente" else None
    return repo.count_drafts(
        db,
        tenant_id,
        client_id=client_id,
        status=status,
        created_by=created_by,
        related_task_id=related_task_id,
        owner_restriction=owner_restriction,
        created_from=created_from,
        created_to=created_to,
    )


def _build_wa_url(e164: str, body: str) -> str:
    digits = e164.lstrip("+")
    encoded_body = urllib.parse.quote(body, safe="")
    return f"https://wa.me/{digits}?text={encoded_body}"


def mark_open_requested(
    db: Session, *, tenant_id: uuid.UUID, actor: User, draft_id: uuid.UUID, expected_version: int
) -> tuple[CommunicationDraft, str]:
    """Idempotente sobre OPEN_REQUESTED: repetir la solicitud conserva el
    `open_requested_at` original, no incrementa `version`, y devuelve de
    nuevo la URL wa.me derivada -- nunca se genera una segunda entrada de
    historial para un no-op (Seccion 10 del pedido)."""
    draft = repo.lock_by_id_for_update(db, tenant_id, draft_id)
    if draft is None:
        raise DraftNotFoundError()
    _ensure_asistente_owns_draft(actor, draft)

    if draft.status not in (
        CommunicationDraftStatus.DRAFT.value,
        CommunicationDraftStatus.OPEN_REQUESTED.value,
    ):
        raise InvalidCommunicationTransitionError(
            draft.status, CommunicationDraftStatus.OPEN_REQUESTED.value
        )
    if draft.version != expected_version:
        raise VersionConflictError(expected_version, draft.version)

    wa_url = _build_wa_url(draft.recipient_phone_e164, draft.body)

    if draft.status == CommunicationDraftStatus.OPEN_REQUESTED.value:
        # No-op idempotente: no se muta nada, no se audita de nuevo.
        return draft, wa_url

    previous_status = draft.status
    previous_version = draft.version
    draft.status = CommunicationDraftStatus.OPEN_REQUESTED.value
    draft.open_requested_at = clock.utcnow()
    draft.version += 1
    draft.updated_at = clock.utcnow()
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.COMMUNICATION_OPEN_REQUEST,
        entity_type="communication_draft",
        entity_id=draft.id,
        metadata={
            "client_id": str(draft.client_id),
            "previous_status": previous_status,
            "new_status": draft.status,
            "previous_version": previous_version,
            "new_version": draft.version,
        },
    )
    return draft, wa_url


def mark_sent(
    db: Session, *, tenant_id: uuid.UUID, actor: User, draft_id: uuid.UUID, expected_version: int
) -> CommunicationDraft:
    draft = repo.lock_by_id_for_update(db, tenant_id, draft_id)
    if draft is None:
        raise DraftNotFoundError()
    _ensure_asistente_owns_draft(actor, draft)

    if draft.status != CommunicationDraftStatus.OPEN_REQUESTED.value:
        raise InvalidCommunicationTransitionError(
            draft.status, CommunicationDraftStatus.MARKED_SENT.value
        )
    if draft.version != expected_version:
        raise VersionConflictError(expected_version, draft.version)

    previous_status = draft.status
    previous_version = draft.version
    now = clock.utcnow()
    draft.status = CommunicationDraftStatus.MARKED_SENT.value
    draft.marked_sent_at = now
    draft.marked_sent_by = actor.id
    draft.version += 1
    draft.updated_at = now
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.COMMUNICATION_MARK_SENT,
        entity_type="communication_draft",
        entity_id=draft.id,
        metadata={
            "client_id": str(draft.client_id),
            "previous_status": previous_status,
            "new_status": draft.status,
            "previous_version": previous_version,
            "new_version": draft.version,
        },
    )
    return draft


def discard_draft(
    db: Session, *, tenant_id: uuid.UUID, actor: User, draft_id: uuid.UUID, expected_version: int
) -> CommunicationDraft:
    draft = repo.lock_by_id_for_update(db, tenant_id, draft_id)
    if draft is None:
        raise DraftNotFoundError()
    _ensure_asistente_owns_draft(actor, draft)

    if draft.status not in (
        CommunicationDraftStatus.DRAFT.value,
        CommunicationDraftStatus.OPEN_REQUESTED.value,
    ):
        raise InvalidCommunicationTransitionError(
            draft.status, CommunicationDraftStatus.DISCARDED.value
        )
    if draft.version != expected_version:
        raise VersionConflictError(expected_version, draft.version)

    previous_status = draft.status
    previous_version = draft.version
    now = clock.utcnow()
    draft.status = CommunicationDraftStatus.DISCARDED.value
    draft.discarded_at = now
    draft.discarded_by = actor.id
    draft.version += 1
    draft.updated_at = now
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.COMMUNICATION_DISCARD,
        entity_type="communication_draft",
        entity_id=draft.id,
        metadata={
            "client_id": str(draft.client_id),
            "previous_status": previous_status,
            "new_status": draft.status,
            "previous_version": previous_version,
            "new_version": draft.version,
        },
    )
    return draft
