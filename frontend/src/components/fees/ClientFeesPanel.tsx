"use client";

import Link from "next/link";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import {
  FEE_AGREEMENT_STATUS_SEMAPHORE,
  FEE_CHARGE_STATUS_SEMAPHORE,
  FEE_COLLECTION_RISK_SEMAPHORE,
  StatusSemaphore,
} from "@/components/ui/StatusSemaphore";
import { apiClient, ApiError } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";
import { computeFeeCollectionRisk } from "@/lib/feeRisk";
import { hasPermission } from "@/lib/permissions";
import type { TaxPeriod } from "@/lib/types/declaration";
import type {
  ProfessionalFeeAgreement,
  ProfessionalFeeCharge,
  ProfessionalFeePayment,
  ProfessionalReceipt,
} from "@/lib/types/professionalFee";

const TODAY = new Date().toISOString().slice(0, 10);

interface AgreementFormState {
  serviceType: string;
  amount: string;
  billingDay: string;
  validFrom: string;
  validUntil: string;
  description: string;
  observations: string;
}

function emptyAgreementForm(): AgreementFormState {
  return {
    serviceType: "",
    amount: "",
    billingDay: "",
    validFrom: new Date().toISOString().slice(0, 10),
    validUntil: "",
    description: "",
    observations: "",
  };
}

export function ClientFeesPanel({ clientId, showHeader = true }: { clientId: string; showHeader?: boolean }) {
  const { user } = useCurrentUser();
  const canWrite = hasPermission(user, "professional_fee:write");
  const canVoid = hasPermission(user, "professional_fee:void");

  const [agreements, setAgreements] = useState<ProfessionalFeeAgreement[]>([]);
  const [periods, setPeriods] = useState<TaxPeriod[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [agreementForm, setAgreementForm] = useState<AgreementFormState>(emptyAgreementForm);
  const [creatingAgreement, setCreatingAgreement] = useState(false);

  const [selectedAgreementId, setSelectedAgreementId] = useState<string | null>(null);
  const [charges, setCharges] = useState<ProfessionalFeeCharge[]>([]);
  const [chargePeriodId, setChargePeriodId] = useState("");
  const [chargeAmount, setChargeAmount] = useState("");
  const [chargeDueDate, setChargeDueDate] = useState("");
  const [creatingCharge, setCreatingCharge] = useState(false);

  const [selectedChargeId, setSelectedChargeId] = useState<string | null>(null);
  const [payments, setPayments] = useState<ProfessionalFeePayment[]>([]);
  const [receipts, setReceipts] = useState<ProfessionalReceipt[]>([]);
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [paymentAmount, setPaymentAmount] = useState("");
  const [paymentMethod, setPaymentMethod] = useState("TRANSFER");
  const [overpaymentConfirmed, setOverpaymentConfirmed] = useState(false);
  const [overpaymentReason, setOverpaymentReason] = useState("");
  const [needsOverpaymentConfirmation, setNeedsOverpaymentConfirmation] = useState(false);
  const [submittingPayment, setSubmittingPayment] = useState(false);
  const [receiptNumber, setReceiptNumber] = useState("");
  const [receiptAmount, setReceiptAmount] = useState("");
  const [submittingReceipt, setSubmittingReceipt] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [agreementsData, periodsData] = await Promise.all([
        apiClient.get<ProfessionalFeeAgreement[]>(`/clients/${clientId}/fee-agreements`),
        apiClient.get<TaxPeriod[]>("/tax-periods"),
      ]);
      setAgreements(agreementsData);
      setPeriods(periodsData);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar los honorarios.");
    } finally {
      setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  const reloadCharges = useCallback(async (agreementId: string) => {
    const data = await apiClient.get<ProfessionalFeeCharge[]>(`/fee-agreements/${agreementId}/charges`);
    setCharges(data);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => {
      if (selectedAgreementId) void reloadCharges(selectedAgreementId);
      else setCharges([]);
    });
  }, [selectedAgreementId, reloadCharges]);

  const reloadChargeDetail = useCallback(async (chargeId: string) => {
    const [paymentsData, receiptsData] = await Promise.all([
      apiClient.get<ProfessionalFeePayment[]>(`/fee-charges/${chargeId}/payments`),
      apiClient.get<ProfessionalReceipt[]>(`/fee-charges/${chargeId}/receipts`),
    ]);
    setPayments(paymentsData);
    setReceipts(receiptsData);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => {
      if (selectedChargeId) void reloadChargeDetail(selectedChargeId);
      else {
        setPayments([]);
        setReceipts([]);
      }
    });
  }, [selectedChargeId, reloadChargeDetail]);

  async function handleCreateAgreement() {
    if (!agreementForm.serviceType.trim() || !agreementForm.amount || !agreementForm.validFrom) return;
    setCreatingAgreement(true);
    setError(null);
    try {
      await apiClient.post(`/clients/${clientId}/fee-agreements`, {
        service_type: agreementForm.serviceType,
        amount: agreementForm.amount,
        billing_day: agreementForm.billingDay ? Number(agreementForm.billingDay) : null,
        included_services: null,
        description: agreementForm.description || null,
        valid_from: agreementForm.validFrom,
        valid_until: agreementForm.validUntil || null,
        observations: agreementForm.observations || null,
      });
      setAgreementForm(emptyAgreementForm());
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo crear el acuerdo.");
    } finally {
      setCreatingAgreement(false);
    }
  }

  async function handleSupersede(agreementId: string) {
    const reason = window.prompt("Motivo para superar este acuerdo:");
    if (!reason || !reason.trim()) return;
    const effectiveDate = window.prompt("Fecha efectiva de cierre (YYYY-MM-DD), vacío = hoy:", TODAY);
    try {
      await apiClient.post(`/fee-agreements/${agreementId}/supersede`, {
        effective_date: effectiveDate || null,
        reason,
      });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo superar el acuerdo.");
    }
  }

  async function handleCancel(agreementId: string) {
    const reason = window.prompt("Motivo para cancelar este acuerdo:");
    if (!reason || !reason.trim()) return;
    const effectiveDate = window.prompt("Fecha efectiva de cancelación (YYYY-MM-DD), vacío = hoy:", TODAY);
    try {
      await apiClient.post(`/fee-agreements/${agreementId}/cancel`, {
        effective_date: effectiveDate || null,
        reason,
      });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cancelar el acuerdo.");
    }
  }

  async function handleCreateCharge() {
    if (!selectedAgreementId || !chargePeriodId) return;
    setCreatingCharge(true);
    setError(null);
    try {
      await apiClient.post(`/fee-agreements/${selectedAgreementId}/charges`, {
        tax_period_id: chargePeriodId,
        amount: chargeAmount || null,
        due_date: chargeDueDate || null,
        observations: null,
      });
      setChargePeriodId("");
      setChargeAmount("");
      setChargeDueDate("");
      await reloadCharges(selectedAgreementId);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo crear el cargo (¿ya existe para ese periodo?).");
    } finally {
      setCreatingCharge(false);
    }
  }

  async function handleIssueCharge(chargeId: string) {
    const dueDate = window.prompt("Fecha de vencimiento (YYYY-MM-DD), vacío = sin vencimiento exigible:");
    try {
      await apiClient.post(`/fee-charges/${chargeId}/issue`, { due_date: dueDate || null });
      if (selectedAgreementId) await reloadCharges(selectedAgreementId);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo emitir el cargo.");
    }
  }

  async function handleCreatePayment() {
    if (!selectedChargeId || !paymentAmount) return;
    setSubmittingPayment(true);
    setError(null);
    try {
      await apiClient.post(`/fee-charges/${selectedChargeId}/payments`, {
        idempotency_key: idempotencyKey,
        amount: paymentAmount,
        paid_at: TODAY,
        payment_method: paymentMethod,
        operation_number: null,
        reference: null,
        observations: null,
        overpayment_confirmed: overpaymentConfirmed,
        overpayment_reason: overpaymentReason || null,
      });
      setPaymentAmount("");
      setOverpaymentConfirmed(false);
      setOverpaymentReason("");
      setNeedsOverpaymentConfirmation(false);
      setIdempotencyKey(crypto.randomUUID());
      await reloadChargeDetail(selectedChargeId);
      if (selectedAgreementId) await reloadCharges(selectedAgreementId);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = String(err.detail ?? "").toLowerCase();
        if (detail.includes("sobrepago") || detail.includes("excede")) {
          setNeedsOverpaymentConfirmation(true);
          setError("El pago excede el saldo del cargo. Confirma el sobrepago e indica el motivo.");
        } else {
          setError("Esta operación ya se envió con datos distintos. Revisa los pagos existentes.");
        }
      } else {
        setError(err instanceof ApiError ? String(err.detail) : "No se pudo registrar el pago.");
      }
    } finally {
      setSubmittingPayment(false);
    }
  }

  async function handleVoidPayment(paymentId: string) {
    const reason = window.prompt("Motivo de la anulación:");
    if (!reason || !reason.trim() || !selectedChargeId) return;
    try {
      await apiClient.post(`/fee-charges/${selectedChargeId}/payments/${paymentId}/void`, { reason });
      await reloadChargeDetail(selectedChargeId);
      if (selectedAgreementId) await reloadCharges(selectedAgreementId);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo anular el pago.");
    }
  }

  async function handleIssueReceipt() {
    if (!selectedChargeId || !receiptNumber.trim() || !receiptAmount) return;
    setSubmittingReceipt(true);
    setError(null);
    try {
      await apiClient.post(`/fee-charges/${selectedChargeId}/receipts`, {
        receipt_type: "RECIBO_HONORARIOS",
        number: receiptNumber,
        issued_at: TODAY,
        amount: receiptAmount,
        document_reference: null,
        observations: null,
      });
      setReceiptNumber("");
      setReceiptAmount("");
      await reloadChargeDetail(selectedChargeId);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo emitir el recibo (¿excede el monto del cargo?).");
    } finally {
      setSubmittingReceipt(false);
    }
  }

  async function handleVoidReceipt(receiptId: string) {
    const reason = window.prompt("Motivo de la anulación del recibo:");
    if (!reason || !reason.trim() || !selectedChargeId) return;
    try {
      await apiClient.post(`/fee-receipts/${receiptId}/void`, { reason });
      await reloadChargeDetail(selectedChargeId);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo anular el recibo.");
    }
  }

  const selectedCharge = charges.find((c) => c.id === selectedChargeId) ?? null;
  const totalPaid = payments.filter((p) => !p.is_voided).reduce((sum, p) => sum + Number(p.amount), 0);
  const balance = selectedCharge ? Number(selectedCharge.amount) - totalPaid : 0;
  const liveRisk = selectedCharge
    ? computeFeeCollectionRisk(selectedCharge.due_date, selectedCharge.status, balance, TODAY)
    : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "960px" }}>
      {showHeader && (
        <div>
          <Link href={`/clients/${clientId}`} style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
            ← Volver al contribuyente
          </Link>
          <h1 style={{ fontSize: "1.4rem", marginTop: "0.4rem" }}>Honorarios profesionales</h1>
        </div>
      )}

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {loading ? (
        <p>Cargando…</p>
      ) : (
        <>
          {canWrite && (
          <section className="surface-card" style={{ padding: "1.25rem" }}>
            <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Nuevo acuerdo</h2>
            <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "flex-end" }}>
              <Input
                label="Tipo de servicio"
                value={agreementForm.serviceType}
                onChange={(e) => setAgreementForm((prev) => ({ ...prev, serviceType: e.target.value }))}
              />
              <Input
                label="Monto"
                type="number"
                step="0.01"
                min="0"
                value={agreementForm.amount}
                onChange={(e) => setAgreementForm((prev) => ({ ...prev, amount: e.target.value }))}
              />
              <Input
                label="Día de facturación (opcional)"
                type="number"
                min="1"
                max="31"
                value={agreementForm.billingDay}
                onChange={(e) => setAgreementForm((prev) => ({ ...prev, billingDay: e.target.value }))}
              />
              <Input
                label="Vigente desde"
                type="date"
                value={agreementForm.validFrom}
                onChange={(e) => setAgreementForm((prev) => ({ ...prev, validFrom: e.target.value }))}
              />
              <Button
                type="button"
                onClick={handleCreateAgreement}
                disabled={creatingAgreement || !agreementForm.serviceType.trim() || !agreementForm.amount}
              >
                {creatingAgreement ? "Creando…" : "Crear acuerdo"}
              </Button>
            </div>
          </section>
          )}

          <section>
            <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Acuerdos</h2>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Servicio</th>
                  <th>Monto</th>
                  <th>Estado</th>
                  <th>Vigencia</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {agreements.map((a) => (
                  <tr key={a.id} style={{ background: selectedAgreementId === a.id ? "var(--border)" : undefined }}>
                    <td>{a.service_type}</td>
                    <td>{a.amount}</td>
                    <td>
                      <StatusSemaphore status={a.status} map={FEE_AGREEMENT_STATUS_SEMAPHORE} />
                    </td>
                    <td>
                      {a.valid_from} → {a.valid_until ?? "indefinido"}
                    </td>
                    <td style={{ display: "flex", gap: "0.4rem" }}>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => setSelectedAgreementId(a.id === selectedAgreementId ? null : a.id)}
                      >
                        {selectedAgreementId === a.id ? "Ocultar cargos" : "Ver cargos"}
                      </Button>
                      {a.status === "ACTIVE" && canWrite && (
                        <>
                          <Button type="button" variant="secondary" onClick={() => handleSupersede(a.id)}>
                            Superar
                          </Button>
                          <Button type="button" variant="danger" onClick={() => handleCancel(a.id)}>
                            Cancelar
                          </Button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
                {agreements.length === 0 && (
                  <tr>
                    <td colSpan={5} style={{ color: "var(--muted)" }}>
                      Este cliente no tiene acuerdos de honorarios todavía.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>

          {selectedAgreementId && (
            <section className="surface-card" style={{ padding: "1.25rem" }}>
              <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Cargos del acuerdo</h2>
              {canWrite && (
              <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap", marginBottom: "1rem" }}>
                <Select
                  label="Periodo tributario"
                  placeholder="Selecciona…"
                  options={periods.map((p) => ({ value: p.id, label: p.display_name ?? p.canonical_code }))}
                  value={chargePeriodId}
                  onChange={(e) => setChargePeriodId(e.target.value)}
                />
                <Input
                  label="Monto (opcional, usa el del acuerdo)"
                  type="number"
                  step="0.01"
                  value={chargeAmount}
                  onChange={(e) => setChargeAmount(e.target.value)}
                />
                <Input
                  label="Vencimiento (opcional)"
                  type="date"
                  value={chargeDueDate}
                  onChange={(e) => setChargeDueDate(e.target.value)}
                />
                <Button type="button" onClick={handleCreateCharge} disabled={creatingCharge || !chargePeriodId}>
                  {creatingCharge ? "Creando…" : "Crear cargo"}
                </Button>
              </div>
              )}

              <table className="data-table">
                <thead>
                  <tr>
                    <th>Monto</th>
                    <th>Estado</th>
                    <th>Vencimiento</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {charges.map((c) => (
                    <tr key={c.id} style={{ background: selectedChargeId === c.id ? "var(--border)" : undefined }}>
                      <td>{c.amount}</td>
                      <td>
                        <StatusSemaphore status={c.status} map={FEE_CHARGE_STATUS_SEMAPHORE} />
                      </td>
                      <td>{c.due_date ?? "—"}</td>
                      <td style={{ display: "flex", gap: "0.4rem" }}>
                        {c.status === "PENDING_ISSUANCE" && canWrite && (
                          <Button type="button" variant="secondary" onClick={() => handleIssueCharge(c.id)}>
                            Emitir
                          </Button>
                        )}
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => setSelectedChargeId(c.id === selectedChargeId ? null : c.id)}
                        >
                          {selectedChargeId === c.id ? "Ocultar detalle" : "Ver detalle"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                  {charges.length === 0 && (
                    <tr>
                      <td colSpan={4} style={{ color: "var(--muted)" }}>
                        Este acuerdo no tiene cargos todavía.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </section>
          )}

          {selectedCharge && (
            <section className="surface-card" style={{ padding: "1.25rem", display: "flex", flexDirection: "column", gap: "1.5rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
                <h2 style={{ fontSize: "1.05rem" }}>Detalle del cargo</h2>
                <span>Saldo: {balance.toFixed(2)}</span>
                {liveRisk && <StatusSemaphore status={liveRisk} map={FEE_COLLECTION_RISK_SEMAPHORE} />}
              </div>

              <div>
                <h3 style={{ fontSize: "0.95rem" }}>Pagos</h3>
                {canWrite && selectedCharge.status !== "PENDING_ISSUANCE" && selectedCharge.status !== "CANCELLED" && (
                  <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "flex-end", marginBottom: "0.75rem" }}>
                    <Input
                      label="Monto"
                      type="number"
                      step="0.01"
                      min="0.01"
                      value={paymentAmount}
                      onChange={(e) => setPaymentAmount(e.target.value)}
                    />
                    <Select
                      label="Método"
                      options={[
                        { value: "TRANSFER", label: "Transferencia" },
                        { value: "CASH", label: "Efectivo" },
                        { value: "CARD", label: "Tarjeta" },
                        { value: "OTHER", label: "Otro" },
                      ]}
                      value={paymentMethod}
                      onChange={(e) => setPaymentMethod(e.target.value)}
                    />
                    <Button type="button" onClick={handleCreatePayment} disabled={submittingPayment || !paymentAmount}>
                      {submittingPayment ? "Registrando…" : "Registrar pago"}
                    </Button>
                    {needsOverpaymentConfirmation && (
                      <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", padding: "0.5rem", border: "1px solid var(--danger)", borderRadius: "6px" }}>
                        <label style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                          <input
                            type="checkbox"
                            checked={overpaymentConfirmed}
                            onChange={(e) => setOverpaymentConfirmed(e.target.checked)}
                          />
                          Confirmo el sobrepago
                        </label>
                        <Input
                          label="Motivo"
                          value={overpaymentReason}
                          onChange={(e) => setOverpaymentReason(e.target.value)}
                        />
                      </div>
                    )}
                  </div>
                )}
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Fecha</th>
                      <th>Monto</th>
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
                        <td>{p.overpayment_confirmed ? `Sí (${p.overpayment_amount})` : "No"}</td>
                        <td>
                          <span className={`status-pill ${p.is_voided ? "status-rojo" : "status-verde"}`}>
                            {p.is_voided ? "Anulado" : "Activo"}
                          </span>
                        </td>
                        <td>
                          {!p.is_voided && canVoid && (
                            <Button type="button" variant="danger" onClick={() => handleVoidPayment(p.id)}>
                              Anular
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                    {payments.length === 0 && (
                      <tr>
                        <td colSpan={5} style={{ color: "var(--muted)" }}>
                          Sin pagos todavía.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <div>
                <h3 style={{ fontSize: "0.95rem" }}>Recibos</h3>
                {canWrite && (
                <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "flex-end", marginBottom: "0.75rem" }}>
                  <Input label="Número" value={receiptNumber} onChange={(e) => setReceiptNumber(e.target.value)} />
                  <Input
                    label="Monto"
                    type="number"
                    step="0.01"
                    min="0.01"
                    value={receiptAmount}
                    onChange={(e) => setReceiptAmount(e.target.value)}
                  />
                  <Button
                    type="button"
                    onClick={handleIssueReceipt}
                    disabled={submittingReceipt || !receiptNumber.trim() || !receiptAmount}
                  >
                    {submittingReceipt ? "Emitiendo…" : "Emitir recibo"}
                  </Button>
                </div>
                )}
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Número</th>
                      <th>Fecha</th>
                      <th>Monto</th>
                      <th>Estado</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {receipts.map((r) => (
                      <tr key={r.id}>
                        <td>{r.number}</td>
                        <td>{r.issued_at}</td>
                        <td>{r.amount}</td>
                        <td>
                          <span className={`status-pill ${r.status === "VOIDED" ? "status-rojo" : "status-verde"}`}>
                            {r.status === "VOIDED" ? "Anulado" : "Emitido"}
                          </span>
                        </td>
                        <td>
                          {r.status === "ISSUED" && canVoid && (
                            <Button type="button" variant="danger" onClick={() => handleVoidReceipt(r.id)}>
                              Anular
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                    {receipts.length === 0 && (
                      <tr>
                        <td colSpan={5} style={{ color: "var(--muted)" }}>
                          Sin recibos todavía.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
