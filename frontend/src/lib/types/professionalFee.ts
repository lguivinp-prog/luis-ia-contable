export type FeeAgreementStatus = "ACTIVE" | "SUPERSEDED" | "CANCELLED";
export type FeeChargeStatus =
  | "PENDING_ISSUANCE"
  | "ISSUED"
  | "PAYMENT_PENDING"
  | "PARTIALLY_PAID"
  | "PAID"
  | "CANCELLED";

export interface ProfessionalFeeAgreement {
  id: string;
  client_id: string;
  service_type: string;
  amount: string;
  currency: string;
  billing_day: number | null;
  included_services: string | null;
  description: string | null;
  status: FeeAgreementStatus;
  valid_from: string;
  valid_until: string | null;
  observations: string | null;
}

export interface ProfessionalFeeCharge {
  id: string;
  agreement_id: string;
  tax_period_id: string;
  amount: string;
  currency: string;
  status: FeeChargeStatus;
  due_date: string | null;
  issued_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  observations: string | null;
}

export interface ProfessionalFeePayment {
  id: string;
  charge_id: string;
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

export interface ProfessionalReceipt {
  id: string;
  charge_id: string;
  issued_by: string;
  receipt_type: string;
  number: string;
  issued_at: string;
  amount: string;
  currency: string;
  status: "ISSUED" | "VOIDED";
  voided_at: string | null;
  void_reason: string | null;
  document_reference: string | null;
  observations: string | null;
}
