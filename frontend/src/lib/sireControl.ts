// Cliente de la API del control SIRE (M4). El frontend habla SOLO con el backend (`/api/v1/sire-controls/*`): nunca con SUNAT, DARWIN ni CONTASIS.
import { apiClient } from "@/lib/apiClient";
import type {
  SireComponent,
  SireControl,
  SireControlsForPeriod,
  SireFile,
  SireFileIntegrity,
  SireHistory,
  SireImportInput,
  SireManualSourceInput,
  SireOrigin,
  SirePreview,
  SireReport,
  SireReview,
  SireSourceCreated,
} from "@/lib/types/sireControl";

const BASE = "/sire-controls";

export function fetchControlsForPeriod(clientId: string, periodId: string): Promise<SireControlsForPeriod> {
  const query = new URLSearchParams({ client_id: clientId, tax_period_id: periodId });
  return apiClient.get<SireControlsForPeriod>(`${BASE}/by-period?${query.toString()}`);
}

export function addManualSource(input: SireManualSourceInput): Promise<SireSourceCreated> {
  return apiClient.post<SireSourceCreated>(`${BASE}/sources/manual`, input);
}

export function previewImportFile(args: {
  clientId: string;
  periodId: string;
  component: SireComponent;
  origin: SireOrigin;
  file: File;
  sheet?: string | null;
  encoding?: string | null;
  delimiter?: string | null;
}): Promise<SirePreview> {
  const form = new FormData();
  form.append("client_id", args.clientId);
  form.append("tax_period_id", args.periodId);
  form.append("component", args.component);
  form.append("origin", args.origin);
  if (args.sheet) form.append("sheet", args.sheet);
  if (args.encoding) form.append("encoding", args.encoding);
  if (args.delimiter) form.append("delimiter", args.delimiter);
  form.append("file", args.file);
  return apiClient.uploadFile<SirePreview>(`${BASE}/files/preview`, form);
}

export function commitImport(input: SireImportInput): Promise<SireSourceCreated> {
  return apiClient.post<SireSourceCreated>(`${BASE}/sources/import`, input);
}

export function attachEvidence(args: { controlId: string; origin: SireOrigin; file: File; description?: string | null; sourceId?: string | null }): Promise<{ created: boolean; file: SireFile }> {
  const form = new FormData();
  form.append("origin", args.origin);
  if (args.description) form.append("description", args.description);
  if (args.sourceId) form.append("source_id", args.sourceId);
  form.append("file", args.file);
  return apiClient.uploadFile<{ created: boolean; file: SireFile }>(`${BASE}/${args.controlId}/files`, form);
}

export function recordReview(controlId: string, input: { decision: string; notes: string | null; comparison_hash: string; idempotency_key: string }): Promise<SireReview> {
  return apiClient.post<SireReview>(`${BASE}/${controlId}/reviews`, input);
}

export function setTolerance(controlId: string, input: { amount: string | null; reason: string | null; expected_row_version: number }): Promise<SireControl> {
  return apiClient.post<SireControl>(`${BASE}/${controlId}/tolerance`, input);
}

export function fetchHistory(controlId: string): Promise<SireHistory> {
  return apiClient.get<SireHistory>(`${BASE}/${controlId}/history`);
}

export function fetchReport(controlId: string): Promise<SireReport> {
  return apiClient.get<SireReport>(`${BASE}/${controlId}/report`);
}

export function verifyFile(controlId: string, fileId: string): Promise<SireFileIntegrity> {
  return apiClient.get<SireFileIntegrity>(`${BASE}/${controlId}/files/${fileId}/verify`);
}

/** Descarga AUTORIZADA (cookie de sesion): el backend registra la descarga en la auditoria. */
export function downloadUrl(controlId: string, fileId: string): string {
  return `/api/v1${BASE}/${controlId}/files/${fileId}/download`;
}
