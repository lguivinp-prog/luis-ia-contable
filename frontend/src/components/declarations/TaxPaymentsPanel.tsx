"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { TAX_PAYMENT_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { ApiError } from "@/lib/apiClient";
import type { TaxAssessment, TaxPayment } from "@/lib/types/taxAssessment";
import type { TaxPaymentStatus } from "@/lib/types/declaration";

const PAYMENT_METHOD_OPTIONS = [
  { value: "TRANSFER", label: "Transferencia" },
  { value: "CASH", label: "Efectivo" },
  { value: "CHECK", label: "Cheque" },
  { value: "CARD", label: "Tarjeta" },
  { value: "OTHER", label: "Otro" },
];

interface PaymentFormState {
  idempotencyKey: string;
  amount: string;
  paidAt: string;
  paymentMethod: string;
  operationNumber: string;
  reference: string;
  observations: string;
  overpaymentConfirmed: boolean;
  overpaymentReason: string;
}

function newForm(): PaymentFormState {
  return {
    idempotencyKey: crypto.randomUUID(),
    amount: "",
    paidAt: new Date().toISOString().slice(0, 10),
    paymentMethod: "TRANSFER",
    operationNumber: "",
    reference: "",
    observations: "",
    overpaymentConfirmed: false,
    overpaymentReason: "",
  };
}

interface TaxPaymentsPanelProps {
  taxPaymentStatus: TaxPaymentStatus;
  payments: TaxPayment[];
  assessments: TaxAssessment[];
  canAct: boolean;
  onCreate: (payload: {
    idempotency_key: string;
    amount: string;
    paid_at: string;
    payment_method: string;
    operation_number: string | null;
    reference: string | null;
    observations: string | null;
    overpayment_confirmed: boolean;
    overpayment_reason: string | null;
  }) => Promise<void>;
  onVoid: (paymentId: string, reason: string) => Promise<void>;
}

export function TaxPaymentsPanel({
  taxPaymentStatus,
  payments,
  assessments,
  canAct,
  onCreate,
  onVoid,
}: TaxPaymentsPanelProps) {
  const [form, setForm] = useState<PaymentFormState>(newForm);
  const [needsOverpaymentConfirmation, setNeedsOverpaymentConfirmation] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voidingId, setVoidingId] = useState<string | null>(null);

  const sequenceByAssessmentId = new Map(assessments.map((a) => [a.id, a.sequence_number]));

  async function handleSubmit() {
    if (!form.amount) return;
    setSubmitting(true);
    setError(null);
    try {
      await onCreate({
        idempotency_key: form.idempotencyKey,
        amount: form.amount,
        paid_at: form.paidAt,
        payment_method: form.paymentMethod,
        operation_number: form.operationNumber || null,
        reference: form.reference || null,
        observations: form.observations || null,
        overpayment_confirmed: form.overpaymentConfirmed,
        overpayment_reason: form.overpaymentReason || null,
      });
      setForm(newForm());
      setNeedsOverpaymentConfirmation(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = String(err.detail ?? "");
        if (detail.toLowerCase().includes("sobrepago") || detail.toLowerCase().includes("excede")) {
          setNeedsOverpaymentConfirmation(true);
          setError("El pago excede el saldo pendiente. Confirma el sobrepago e indica el motivo para reintentar.");
        } else {
          setError(
            "Esta operación ya se envió con datos distintos (o hay una carrera de concurrencia). " +
              "Revisa los pagos existentes antes de reintentar.",
          );
        }
      } else {
        setError(err instanceof ApiError ? String(err.detail) : "No se pudo registrar el pago.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVoid(paymentId: string) {
    const reason = window.prompt("Motivo de la anulación:");
    if (!reason || !reason.trim()) return;
    setVoidingId(paymentId);
    setError(null);
    try {
      await onVoid(paymentId, reason);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo anular el pago.");
    } finally {
      setVoidingId(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
        <strong>Saldo tributario:</strong>
        <StatusSemaphore status={taxPaymentStatus} map={TAX_PAYMENT_STATUS_SEMAPHORE} />
      </div>

      {canAct && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", maxWidth: "360px" }}>
          <h3 style={{ fontSize: "1rem" }}>Registrar pago</h3>
          <Input
            label="Monto"
            type="number"
            step="0.01"
            min="0.01"
            value={form.amount}
            onChange={(e) => setForm((prev) => ({ ...prev, amount: e.target.value }))}
          />
          <Input
            label="Fecha de pago"
            type="date"
            value={form.paidAt}
            onChange={(e) => setForm((prev) => ({ ...prev, paidAt: e.target.value }))}
          />
          <Select
            label="Método de pago"
            options={PAYMENT_METHOD_OPTIONS}
            value={form.paymentMethod}
            onChange={(e) => setForm((prev) => ({ ...prev, paymentMethod: e.target.value }))}
          />
          <Input
            label="Número de operación"
            value={form.operationNumber}
            onChange={(e) => setForm((prev) => ({ ...prev, operationNumber: e.target.value }))}
          />
          <Input
            label="Referencia"
            value={form.reference}
            onChange={(e) => setForm((prev) => ({ ...prev, reference: e.target.value }))}
          />
          <Input
            label="Observaciones"
            value={form.observations}
            onChange={(e) => setForm((prev) => ({ ...prev, observations: e.target.value }))}
          />

          {needsOverpaymentConfirmation && (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", padding: "0.75rem", border: "1px solid var(--danger)", borderRadius: "6px" }}>
              <label style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={form.overpaymentConfirmed}
                  onChange={(e) => setForm((prev) => ({ ...prev, overpaymentConfirmed: e.target.checked }))}
                />
                Confirmo que este pago genera un sobrepago
              </label>
              <Input
                label="Motivo del sobrepago"
                value={form.overpaymentReason}
                onChange={(e) => setForm((prev) => ({ ...prev, overpaymentReason: e.target.value }))}
              />
            </div>
          )}

          <Button type="button" onClick={handleSubmit} disabled={submitting || !form.amount}>
            {submitting ? "Registrando…" : "Registrar pago"}
          </Button>
          {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
        </div>
      )}

      {payments.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>Todavía no hay pagos registrados.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Monto</th>
              <th>Método</th>
              <th>Determinación vigente al pagar</th>
              <th>Sobrepago</th>
              <th>Estado</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {payments.map((p) => (
              <tr key={p.id}>
                <td>{p.paid_at}</td>
                <td>{p.amount}</td>
                <td>{PAYMENT_METHOD_OPTIONS.find((o) => o.value === p.payment_method)?.label ?? p.payment_method}</td>
                <td>#{sequenceByAssessmentId.get(p.assessment_id) ?? "?"}</td>
                <td>{p.overpayment_confirmed ? `Sí (${p.overpayment_amount})` : "No"}</td>
                <td>
                  <span className={`status-pill ${p.is_voided ? "status-rojo" : "status-verde"}`}>
                    {p.is_voided ? "Anulado" : "Activo"}
                  </span>
                </td>
                <td>
                  {!p.is_voided && canAct && (
                    <Button
                      type="button"
                      variant="danger"
                      disabled={voidingId === p.id}
                      onClick={() => handleVoid(p.id)}
                    >
                      {voidingId === p.id ? "Anulando…" : "Anular"}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
