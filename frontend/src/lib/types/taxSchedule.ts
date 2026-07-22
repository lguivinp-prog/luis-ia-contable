export type TaxScheduleVersionStatus =
  | "DRAFT"
  | "IMPORTED"
  | "PENDING_REVIEW"
  | "APPROVED"
  | "SUPERSEDED"
  | "REJECTED"
  | "CANCELLED";

export type ImportRunStatus = "VALIDATED" | "REJECTED" | "CONFIRMED";

export interface TaxScheduleSource {
  id: string;
  obligation_id: string;
  name: string;
  description: string | null;
  reference_url: string | null;
  is_active: boolean;
}

export interface TaxScheduleVersion {
  id: string;
  source_id: string;
  version_label: string;
  fiscal_year: number;
  schedule_type: string;
  scope_category_code: string | null;
  status: TaxScheduleVersionStatus;
  normative_reference: string | null;
  source_document_url: string | null;
  checksum: string | null;
  superseded_by_version_id: string | null;
  observations: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
}

export interface TaxScheduleEntry {
  id: string;
  tax_period: string;
  applicable_last_digits: number[] | null;
  required_category_code: string | null;
  priority: number | null;
  due_date_original: string;
  due_date_effective: string;
  due_date_type: string;
  notes: string | null;
}

export interface ApprovalHistoryEntry {
  id: string;
  from_status: string | null;
  to_status: string;
  actor_user_id: string | null;
  comment: string | null;
  created_at: string;
}

export interface CompareVersionsDiffItem {
  tax_period: string;
  applicable_last_digits: number[] | null;
  required_category_code?: string | null;
  from_due_date_effective?: string;
  to_due_date_effective?: string;
}

export interface CompareVersionsResult {
  version_a: string;
  version_b: string;
  added: CompareVersionsDiffItem[];
  removed: CompareVersionsDiffItem[];
  modified: CompareVersionsDiffItem[];
}

export interface CsvValidationErrorRow {
  row: number;
  message: string;
}

export interface DiffSummary {
  has_previous: boolean;
  previous_version_label?: string;
  added: { tax_period: string; applicable_last_digits: number[] | null; required_category_code: string | null }[];
  removed: { tax_period: string; applicable_last_digits: number[] | null; required_category_code: string | null }[];
  modified_dates: { tax_period: string; from_due_date_effective: string; to_due_date_effective: string }[];
  warnings: string[];
}

export interface ImportRun {
  id: string;
  source_id: string;
  version_id: string | null;
  original_filename: string;
  file_checksum: string;
  fiscal_year: number;
  schedule_type: string;
  scope_category_code: string | null;
  row_count: number | null;
  status: ImportRunStatus;
  validation_errors: CsvValidationErrorRow[] | null;
  diff_summary: DiffSummary | null;
  parsed_rows: Record<string, unknown>[] | null;
  created_at: string;
}

export interface PreviewDueDateResult {
  version_id: string;
  version_label: string;
  source_name: string;
  rule_type: string;
  last_digit: number;
  matched_category_code: string | null;
  due_date_original: string | null;
  due_date_effective: string;
  due_date_type: string;
  is_extension: boolean;
  warnings: string[];
  demo_disclaimer: string;
}
