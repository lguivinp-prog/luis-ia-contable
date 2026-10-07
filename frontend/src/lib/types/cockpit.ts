// Espejo de `backend/app/schemas/cockpit.py` (M1, Domain B). Todos los estados son DERIVADOS por el backend; el
// frontend solo los presenta (nunca recalcula fase, SIRE ni riesgo).

export type CockpitMonthlyPhase = "PRE_APERTURA" | "APERTURA" | "OPERACION" | "CIERRE";

export type CockpitSireStatus =
  | "NOT_CONFIGURED"
  | "NOT_STARTED"
  | "IN_PROGRESS"
  | "READY_FOR_SUNAT"
  | "PRESENTED"
  | "NOT_APPLICABLE";

export type CockpitOverallRisk =
  | "CRITICAL"
  | "OVERDUE"
  | "DUE_TODAY"
  | "PENDING_TAX"
  | "PENDING_DOCUMENTS"
  | "PERIOD_NOT_GENERATED"
  | "CLOSED_WITH_PENDING"
  | "PENDING_FEES"
  | "UPCOMING"
  | "ON_TRACK"
  | "CLOSED"
  | "NOT_APPLICABLE";

export type CockpitFilingProgress = "NOT_GENERATED" | "NOT_APPLICABLE" | "NONE_FILED" | "PARTIAL" | "ALL_FILED";

export interface CockpitPeriod {
  id: string;
  period_type: string;
  canonical_code: string;
  display_name: string | null;
  start_date: string;
  end_date: string;
  is_open: boolean;
}

export interface CockpitSummary {
  active_clients: number;
  period_not_generated: number;
  due_soon: number;
  overdue: number;
  documents_incomplete: number;
  sire_pending: number;
  declarations_pending: number;
  tax_payment_pending: number;
  critical: number;
}

export interface CockpitRow {
  client_id: string;
  business_name: string;
  ruc: string;
  ruc_last_digit: number;
  service_status: string;
  tax_period_id: string;
  tax_period_code: string;
  tax_period_display: string | null;
  monthly_phase: CockpitMonthlyPhase;
  period_generated: boolean;
  nearest_due_date: string | null;
  declaration_count: number;
  filed_declaration_count: number;
  pending_declaration_count: number;
  filing_progress: CockpitFilingProgress;
  document_status: string | null;
  missing_documents_count: number;
  sire_status: CockpitSireStatus | null;
  sire_rvie_status: CockpitSireStatus | null;
  sire_rce_status: CockpitSireStatus | null;
  tax_total_assessed: string | null;
  tax_total_payable: string | null;
  tax_total_paid: string;
  tax_balance: string | null;
  tax_payment_status: string | null;
  fee_total_amount: string;
  fee_total_paid: string;
  fee_balance: string;
  fee_worst_collection_risk: string | null;
  overall_risk: CockpitOverallRisk;
  declaration_worst_risk: string | null;
}

export interface CockpitOut {
  period: CockpitPeriod;
  today: string;
  summary: CockpitSummary;
  items: CockpitRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface CockpitQuery {
  tax_period_id: string;
  search?: string;
  overall_risk?: string;
  sire_status?: string;
  document_status?: string;
  filing_progress?: string;
  limit?: number;
  offset?: number;
}
