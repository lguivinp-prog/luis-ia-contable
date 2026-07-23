export type AssessmentType = "ORIGINAL" | "CORRECTION";

export interface TaxAssessment {
  id: string;
  declaration_id: string;
  assessment_type: AssessmentType;
  sequence_number: number;
  gross_tax_amount: string;
  credits_applied: string;
  total_payable: string;
  remaining_credit: string;
  currency: string;
  assessed_at: string;
  assessed_by: string;
  observations: string | null;
  is_current: boolean;
}

export interface TaxPayment {
  id: string;
  declaration_id: string;
  assessment_id: string;
  paid_at: string;
  amount: string;
  payment_method: string;
  operation_number: string | null;
  reference: string | null;
  observations: string | null;
  overpayment_confirmed: boolean;
  overpayment_reason: string | null;
  overpayment_amount: string | null;
  is_voided: boolean;
  voided_at: string | null;
  void_reason: string | null;
  idempotency_key: string;
  created_at: string;
}
