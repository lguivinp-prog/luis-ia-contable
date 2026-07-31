// Comunicaciones asistidas por WhatsApp (Fase 3, Bloque 3.2, ver ADR-0067) --
// espejo exacto de app/schemas/communication.py. El sistema nunca envía
// nada por sí mismo: el usuario abre WhatsApp manualmente y envía el
// mensaje a mano, por eso los estados se llaman "solicitado"/"marcado por
// ti", nunca "enviado" ni "entregado".

export type CommunicationChannel = "WHATSAPP";

export type CommunicationDraftStatus = "DRAFT" | "OPEN_REQUESTED" | "MARKED_SENT" | "DISCARDED";

export interface TemplateOut {
  code: string;
  version: number;
  display_name: string;
  description: string;
  variables: string[];
}

export interface TemplatesListOut {
  items: TemplateOut[];
}

export interface TemplatePreviewRequest {
  client_id: string;
  related_task_id?: string | null;
  related_document_file_id?: string | null;
  variables: Record<string, string>;
}

export interface TemplatePreviewResponse {
  template_code: string;
  template_version: number;
  body_rendered: string;
  recipient_phone_display: string;
  recipient_phone_e164: string;
  warnings: string[];
}

export interface CommunicationDraftOut {
  id: string;
  client_id: string;
  related_task_id: string | null;
  related_document_file_id: string | null;
  channel: CommunicationChannel;
  template_code: string | null;
  template_version: number | null;
  body: string;
  recipient_phone_display: string;
  status: CommunicationDraftStatus;
  created_by: string;
  open_requested_at: string | null;
  marked_sent_at: string | null;
  marked_sent_by: string | null;
  discarded_at: string | null;
  discarded_by: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CommunicationDraftListOut {
  items: CommunicationDraftOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface MarkOpenRequestedResponse {
  draft: CommunicationDraftOut;
  wa_url: string;
  message: string;
}

export interface MarkSentResponse {
  draft: CommunicationDraftOut;
  message: string;
}

export interface DiscardResponse {
  draft: CommunicationDraftOut;
  message: string;
}
