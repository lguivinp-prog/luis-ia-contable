export type DocumentItemStatus =
  | "NOT_REQUESTED"
  | "REQUESTED"
  | "PARTIAL"
  | "RECEIVED"
  | "OBSERVED"
  | "ACCEPTED"
  | "NOT_APPLICABLE";

export interface DocumentRequirementCatalog {
  id: string;
  tenant_id: string | null;
  is_system: boolean;
  code: string;
  name: string;
  description: string | null;
  category: string | null;
  is_active: boolean;
}

export interface ClientDocumentRequirement {
  id: string;
  client_id: string;
  requirement_catalog_id: string;
  client_obligation_id: string | null;
  is_mandatory: boolean;
  periodicity: string;
  valid_from: string;
  valid_until: string | null;
  description: string | null;
  responsible_user_id: string | null;
  reminder_lead_days: number;
  is_active: boolean;
}

export interface DeclarationDocumentItem {
  id: string;
  client_id: string;
  declaration_id: string;
  client_document_requirement_id: string;
  status: DocumentItemStatus;
  requested_at: string | null;
  received_at: string | null;
  reviewed_at: string | null;
  observations: string | null;
  responsible_user_id: string | null;
  reminder_count: number;
  last_reminder_at: string | null;
  file_metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface SyncRequirementsResult {
  created: string[];
  existing: string[];
  skipped: string[];
  errors: string[];
}

export type DeclarationDocumentFileStatus = "ACTIVE" | "SUPERSEDED" | "DELETED";

// Evidencia fisica real de un item documental (Bloque 5, ADR-0053 a
// ADR-0056). Nunca incluye storage_key/ruta fisica -- ver DeclarationDocumentFileOut.
export interface DeclarationDocumentFile {
  id: string;
  logical_file_id: string;
  version: number;
  declaration_document_item_id: string;
  original_filename: string;
  description: string | null;
  mime_type: string;
  extension: string;
  size_bytes: number;
  sha256_hash: string;
  status: DeclarationDocumentFileStatus;
  replaces_file_id: string | null;
  uploaded_by: string;
  uploaded_at: string;
  superseded_at: string | null;
  deleted_at: string | null;
  deleted_by: string | null;
  deletion_reason: string | null;
}
