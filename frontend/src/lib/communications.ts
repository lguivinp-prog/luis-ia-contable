import { apiClient } from "@/lib/apiClient";
import type {
  CommunicationDraftListOut,
  CommunicationDraftOut,
  DiscardResponse,
  MarkOpenRequestedResponse,
  MarkSentResponse,
  TemplatePreviewRequest,
  TemplatePreviewResponse,
  TemplatesListOut,
} from "@/lib/types/communication";

export function listTemplates(): Promise<TemplatesListOut> {
  return apiClient.get<TemplatesListOut>("/communications/templates");
}

export function previewTemplate(
  templateCode: string,
  payload: TemplatePreviewRequest,
): Promise<TemplatePreviewResponse> {
  return apiClient.post<TemplatePreviewResponse>(
    `/communications/templates/${templateCode}/preview`,
    payload,
  );
}

export interface CommunicationDraftCreatePayload {
  client_id: string;
  related_task_id?: string | null;
  related_document_file_id?: string | null;
  template_code?: string | null;
  template_version?: number | null;
  body: string;
  recipient_phone?: string | null;
}

export function createCommunicationDraft(
  payload: CommunicationDraftCreatePayload,
): Promise<CommunicationDraftOut> {
  return apiClient.post<CommunicationDraftOut>("/communications/drafts", payload);
}

export interface CommunicationDraftFilters {
  client_id?: string;
  status?: string;
  created_by?: string;
  related_task_id?: string;
  limit?: number;
  offset?: number;
}

export function buildCommunicationDraftQuery(filters: CommunicationDraftFilters): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const qs = query.toString();
  return qs ? `?${qs}` : "";
}

export function listCommunicationDrafts(
  filters: CommunicationDraftFilters,
): Promise<CommunicationDraftListOut> {
  return apiClient.get<CommunicationDraftListOut>(
    `/communications/drafts${buildCommunicationDraftQuery(filters)}`,
  );
}

export function getCommunicationDraft(draftId: string): Promise<CommunicationDraftOut> {
  return apiClient.get<CommunicationDraftOut>(`/communications/drafts/${draftId}`);
}

export function markOpenRequested(
  draftId: string,
  expectedVersion: number,
): Promise<MarkOpenRequestedResponse> {
  return apiClient.post<MarkOpenRequestedResponse>(
    `/communications/drafts/${draftId}/mark-open-requested`,
    { expected_version: expectedVersion },
  );
}

export function markSent(draftId: string, expectedVersion: number): Promise<MarkSentResponse> {
  return apiClient.post<MarkSentResponse>(`/communications/drafts/${draftId}/mark-sent`, {
    expected_version: expectedVersion,
  });
}

export function discardDraft(draftId: string, expectedVersion: number): Promise<DiscardResponse> {
  return apiClient.post<DiscardResponse>(`/communications/drafts/${draftId}/discard`, {
    expected_version: expectedVersion,
  });
}
