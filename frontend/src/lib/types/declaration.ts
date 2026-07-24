export type TaxPeriodType = "MONTHLY" | "ANNUAL" | "SPECIAL";

export interface TaxPeriod {
  id: string;
  period_type: TaxPeriodType;
  fiscal_year: number;
  month: number | null;
  canonical_code: string;
  schedule_tax_period_code: string | null;
  display_name: string | null;
  start_date: string;
  end_date: string;
  is_open: boolean;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export type WorkflowStatus =
  | "PENDING"
  | "DOCUMENTS_REQUESTED"
  | "INFORMATION_INCOMPLETE"
  | "DOCUMENTS_COMPLETE"
  | "IN_PROGRESS"
  | "UNDER_REVIEW"
  | "READY_TO_FILE"
  | "CLOSED"
  | "NOT_APPLICABLE"
  | "CANCELLED";

export type FilingStatus = "NOT_FILED" | "READY" | "FILED" | "FAILED" | "NOT_REQUIRED";
export type TaxPaymentStatus =
  | "NOT_DETERMINED"
  | "NO_TAX_DUE"
  | "CREDIT_BALANCE"
  | "PAYMENT_PENDING"
  | "PARTIALLY_PAID"
  | "PAID"
  | "NOT_APPLICABLE";
export type DocumentStatus = "NOT_CONFIGURED" | "PENDING" | "PARTIAL" | "OBSERVED" | "COMPLETE" | "NOT_APPLICABLE";
export type RiskStatus =
  | "CANCELLED"
  | "NOT_APPLICABLE"
  | "CLOSED"
  | "CLOSED_WITH_PENDING"
  | "ON_TRACK"
  | "PENDING_TAX"
  | "PENDING_DOCUMENTS"
  | "CRITICAL"
  | "OVERDUE"
  | "DUE_TODAY"
  | "UPCOMING";

export interface Declaration {
  id: string;
  client_id: string;
  client_obligation_id: string;
  tax_period_id: string;
  assigned_user_id: string | null;
  workflow_status: WorkflowStatus;
  filing_status: FilingStatus;
  tax_payment_status: TaxPaymentStatus;
  document_status: DocumentStatus;
  initial_effective_due_date: string;
  current_original_due_date: string;
  current_effective_due_date: string;
  presentation_date: string | null;
  order_number: string | null;
  filing_reference: string | null;
  ready_to_file_override_reason: string | null;
  closed_at: string | null;
  closed_with_pending: boolean;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  observations: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
  computed_risk_status: RiskStatus | null;
}

export type GenerationRunStatus =
  | "PENDING"
  | "PREVIEWED"
  | "STALE"
  | "CANCELLED"
  | "COMPLETED"
  | "COMPLETED_WITH_ERRORS"
  | "FAILED";

export type RunItemClassification =
  | "APTO"
  | "YA_EXISTENTE"
  | "CLIENTE_INACTIVO"
  | "OBLIGACION_INACTIVA"
  | "SIN_CRONOGRAMA"
  | "AMBIGUO"
  | "SIN_REGLA_APLICABLE"
  | "ERROR_INESPERADO";

export interface DeclarationGenerationRunItem {
  id: string;
  client_id: string;
  client_obligation_id: string;
  classification: RunItemClassification;
  declaration_id: string | null;
  error_detail: string | null;
  excluded_by_user: boolean;
  created_at: string;
  preview_schedule_rule_type: string | null;
  preview_original_due_date: string | null;
  preview_effective_due_date: string | null;
  preview_resolution_hash: string | null;
}

export interface DeclarationGenerationRun {
  id: string;
  tax_period_id: string;
  requested_by: string;
  requested_at: string;
  started_at: string | null;
  preview_checksum: string | null;
  stale_diff: Record<string, unknown>[] | null;
  status: GenerationRunStatus;
  total_analyzed: number;
  total_created: number;
  total_skipped: number;
  total_failed: number;
  errors: Record<string, unknown>[] | null;
  completed_at: string | null;
  items: DeclarationGenerationRunItem[];
}

export interface DashboardRow extends Declaration {
  // Agregado tributario 1:1 por declaration_id.
  tax_gross_amount: string | null;
  tax_total_payable: string | null;
  tax_total_paid: string;
  missing_documents_count: number;

  // Agregado de honorarios por (client_id, tax_period_id), repetido -- nunca
  // multiplicado -- en cada declaracion del mismo cliente/periodo.
  fee_total_charges: number;
  fee_total_amount: string;
  fee_total_paid: string;
  fee_balance: string;
  fee_pending_charges_count: number;
  fee_worst_collection_risk: string | null;
}

export interface DashboardOut {
  items: DashboardRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface DeclarationHistoryOut {
  status_history: {
    id: string;
    status_field: string;
    from_value: string | null;
    to_value: string;
    created_at: string;
    reason: string | null;
    origin: string;
  }[];
  due_date_history: {
    id: string;
    from_effective_due_date: string | null;
    to_effective_due_date: string;
    created_at: string;
    reason: string | null;
    origin: string;
  }[];
  assignment_history: {
    id: string;
    from_user_id: string | null;
    to_user_id: string | null;
    created_at: string;
    reason: string | null;
  }[];
  filing_history: {
    id: string;
    from_filing_status: string | null;
    to_filing_status: string;
    presentation_date: string | null;
    order_number: string | null;
    filing_reference: string | null;
    created_at: string;
    reason: string | null;
    origin: string;
  }[];
  reopen_history: {
    id: string;
    from_workflow_status: string;
    to_workflow_status: string;
    created_at: string;
    reason: string | null;
  }[];
}
