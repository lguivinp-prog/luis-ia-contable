import type { CommunicationDraftCreatePayload } from "@/lib/communications";

export interface CommunicationDraftForm {
  clientId: string;
  relatedTaskId: string;
  relatedDocumentFileId: string;
  templateCode: string;
  templateVersion: number | null;
  body: string;
  recipientPhone: string;
}

/**
 * Construye el payload de POST /communications/drafts. Los campos opcionales
 * (`related_task_id`, `related_document_file_id`, `template_code`,
 * `recipient_phone`) se OMITEN por completo del objeto cuando el formulario
 * los deja vacíos -- nunca se envían como cadena vacía ni `null` explícito,
 * mismo criterio que `buildTaskCreatePayload`.
 */
export function buildCommunicationDraftPayload(
  form: CommunicationDraftForm,
): CommunicationDraftCreatePayload {
  const payload: CommunicationDraftCreatePayload = {
    client_id: form.clientId,
    body: form.body.trim(),
  };
  if (form.relatedTaskId) {
    payload.related_task_id = form.relatedTaskId;
  }
  if (form.relatedDocumentFileId) {
    payload.related_document_file_id = form.relatedDocumentFileId;
  }
  if (form.templateCode) {
    payload.template_code = form.templateCode;
    payload.template_version = form.templateVersion ?? null;
  }
  if (form.recipientPhone.trim()) {
    payload.recipient_phone = form.recipientPhone.trim();
  }
  return payload;
}
