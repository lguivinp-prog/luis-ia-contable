"""Esquemas de comunicaciones asistidas por WhatsApp (Fase 3, Bloque 3.2,
ver ADR-0067)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TemplateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    version: int
    display_name: str
    description: str
    variables: list[str]


class TemplatesListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TemplateOut]


class TemplatePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    related_task_id: UUID | None = None
    related_document_file_id: UUID | None = None
    variables: dict[str, str] = Field(default_factory=dict)


class TemplatePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_code: str
    template_version: int
    body_rendered: str
    recipient_phone_display: str
    recipient_phone_e164: str
    warnings: list[str]


class CommunicationDraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    related_task_id: UUID | None = None
    related_document_file_id: UUID | None = None
    template_code: str | None = None
    template_version: int | None = None
    body: str
    recipient_phone: str | None = None

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body no puede estar vacio")
        if len(value.strip()) > 4000:
            raise ValueError("body no puede exceder 4000 caracteres")
        return value


class CommunicationDraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: UUID
    related_task_id: UUID | None
    related_document_file_id: UUID | None
    channel: Literal["WHATSAPP"]
    template_code: str | None
    template_version: int | None
    body: str
    recipient_phone_display: str
    status: Literal["DRAFT", "OPEN_REQUESTED", "MARKED_SENT", "DISCARDED"]
    created_by: UUID
    open_requested_at: datetime | None
    marked_sent_at: datetime | None
    marked_sent_by: UUID | None
    discarded_at: datetime | None
    discarded_by: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


class CommunicationDraftListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CommunicationDraftOut]
    total: int
    limit: int
    offset: int


class ExpectedVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int


class MarkOpenRequestedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: CommunicationDraftOut
    wa_url: str
    message: str


class MarkSentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: CommunicationDraftOut
    message: str


class DiscardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: CommunicationDraftOut
    message: str
