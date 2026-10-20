// M4 -- control, conciliacion y evidencia del SIRE. Los importes viajan como CADENAS decimales (nunca number): el frontend no calcula ni redondea importes.

export type SireComponent = "RVIE" | "RCE";
export type SireOrigin = "SUNAT" | "DARWIN";
export type SireCurrency = "PEN" | "USD";
export type SireDecision = "APPROVED" | "OBSERVED" | "REQUIRES_REGULARIZATION";
export type SireConcept = "record_count" | "base_amount" | "igv_amount" | "total_amount";

export type SireSummaryCode =
  | "NO_CONTROL"
  | "NO_SOURCES"
  | "PARTIAL"
  | "NOT_COMPARABLE"
  | "MATCH"
  | "DIFFERENCES"
  | "MATCH_REVIEWED"
  | "DIFFERENCES_REVIEWED"
  | "NEEDS_REVIEW";

export interface SireSource {
  id: string;
  origin: SireOrigin;
  entry_mode: "MANUAL" | "IMPORT";
  version: number;
  status: "ACTIVE" | "SUPERSEDED";
  currency: SireCurrency;
  record_count: number | null;
  base_amount: string | null;
  igv_amount: string | null;
  total_amount: string | null;
  source_label: string;
  source_date: string;
  notes: string | null;
  file_id: string | null;
  import_summary: Record<string, unknown> | null;
  replaces_source_id: string | null;
  created_by: string;
  created_at: string;
  superseded_at: string | null;
}

export interface SireFile {
  id: string;
  origin: SireOrigin;
  purpose: "SOURCE_FILE" | "EVIDENCE";
  status: "STAGED" | "ACTIVE" | "SUPERSEDED";
  source_id: string | null;
  original_filename: string;
  description: string | null;
  mime_type: string;
  size_bytes: number;
  sha256_hash: string;
  uploaded_by: string;
  uploaded_at: string;
}

export interface SireConceptResult {
  concept: SireConcept;
  sunat: string | null;
  darwin: string | null;
  difference: string | null; // DARWIN - SUNAT
  comparable: boolean;
  matches: boolean | null;
  exact: boolean | null;
  within_tolerance: boolean;
}

export interface SireComparison {
  status: "NO_SOURCES" | "PARTIAL" | "MATCH" | "DIFFERENCES" | "NOT_COMPARABLE";
  comparison_hash: string;
  currency_mismatch: boolean;
  single_source: SireOrigin | null;
  one_sided_concepts: string[];
  tolerance: string | null;
  tolerance_applied: boolean;
  messages: string[];
  concepts: SireConceptResult[];
}

export interface SireReview {
  id: string;
  version: number;
  status: "CURRENT" | "SUPERSEDED";
  decision: SireDecision;
  notes: string | null;
  comparison_status: string;
  comparison_hash: string;
  comparison_snapshot: Record<string, unknown>;
  sunat_source_id: string | null;
  darwin_source_id: string | null;
  reviewed_by: string;
  reviewed_by_name: string | null;
  reviewed_at: string;
  superseded_at: string | null;
}

export interface SireControl {
  exists: boolean;
  id: string | null;
  client_id: string;
  tax_period_id: string;
  component: SireComponent;
  summary_code: SireSummaryCode;
  row_version: number | null;
  tolerance_amount: string | null;
  tolerance_reason: string | null;
  tolerance_set_at: string | null;
  sunat_source: SireSource | null;
  darwin_source: SireSource | null;
  comparison: SireComparison;
  current_review: SireReview | null;
  review_stale: boolean;
  files: SireFile[];
  history_counts: { sources: number; reviews: number; files: number };
  created_at: string | null;
}

export interface SireControlsForPeriod {
  client_id: string;
  tax_period_id: string;
  tax_period_code: string;
  controls: SireControl[];
  disclaimer: string;
}

export interface SireManualSourceInput {
  client_id: string;
  tax_period_id: string;
  component: SireComponent;
  origin: SireOrigin;
  currency: SireCurrency;
  record_count: number | null;
  base_amount: string | null;
  igv_amount: string | null;
  total_amount: string | null;
  source_label: string;
  source_date: string;
  notes: string | null;
  replaces_source_id: string | null;
  idempotency_key: string;
}

export interface SireSourceCreated {
  created: boolean;
  source: SireSource;
  control: SireControl;
}

export interface SireImportMapping {
  record_count: string | null;
  base_amount: string | null;
  igv_amount: string | null;
  total_amount: string | null;
  period: string | null;
  record_type: string | null;
}

export interface SirePreview {
  control_id: string;
  file: SireFile;
  duplicate_of_existing: boolean;
  kind: "csv" | "xlsx";
  sheets: string[];
  sheet: string | null;
  encoding: string | null;
  delimiter: string | null;
  headers: string[];
  sample_rows: string[][];
  truncated: boolean;
  suggested_mapping: Partial<Record<keyof SireImportMapping, string | null>>;
  decimal_style_detected: "DOT" | "COMMA" | null;
  decimal_style_needs_choice: boolean;
}

export interface SireImportInput {
  file_id: string;
  mode: "DETAIL" | "SUMMARY";
  decimal_style: "AUTO" | "DOT" | "COMMA";
  mapping: SireImportMapping;
  ignore_total_rows: boolean;
  sheet: string | null;
  encoding: "utf-8-sig" | "cp1252" | null;
  delimiter: "," | ";" | "\t" | "|" | null;
  currency: SireCurrency;
  source_label: string;
  source_date: string;
  notes: string | null;
  replaces_source_id: string | null;
  idempotency_key: string;
}

export interface SireHistoryItem {
  kind: "SOURCE" | "REVIEW" | "FILE";
  at: string;
  version: number | null;
  origin: string | null;
  status: string;
  label: string;
  by: string | null;
  ref_id: string;
}

export interface SireHistory {
  control_id: string;
  items: SireHistoryItem[];
  sources: SireSource[];
  reviews: SireReview[];
  files: SireFile[];
}

export interface SireFileIntegrity {
  file_id: string;
  stored_sha256: string;
  computed_sha256: string | null;
  intact: boolean;
  present: boolean;
}

export interface SireReport {
  report_version: number;
  generated_at: string;
  report_hash: string;
  disclaimer: string;
  identification: Record<string, string | number | null>;
  sources: Record<string, Record<string, unknown> | null>;
  comparison: {
    status: string;
    comparison_hash: string;
    tolerance: string | null;
    tolerance_reason: string | null;
    tolerance_applied: boolean;
    concepts: SireConceptResult[];
    one_sided_concepts: string[];
    currency_mismatch: boolean;
  };
  differences: SireConceptResult[];
  files: Array<{ id: string; origin: string; purpose: string; status: string; original_filename: string; mime_type: string; size_bytes: number; sha256: string; uploaded_at: string; source_id: string | null }>;
  review: Record<string, unknown> | null;
  review_history: Array<Record<string, unknown> | null>;
  status_legend: Record<string, string>;
}
