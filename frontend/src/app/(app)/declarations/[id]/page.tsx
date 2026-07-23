"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import {
  FILING_STATUS_SEMAPHORE,
  RISK_STATUS_SEMAPHORE,
  StatusSemaphore,
  WORKFLOW_STATUS_SEMAPHORE,
} from "@/components/ui/StatusSemaphore";
import { ClosePanel } from "@/components/declarations/ClosePanel";
import { DocumentsPanel } from "@/components/declarations/DocumentsPanel";
import { TaxAssessmentPanel } from "@/components/declarations/TaxAssessmentPanel";
import { TaxPaymentsPanel } from "@/components/declarations/TaxPaymentsPanel";
import { fetchCurrentUser } from "@/lib/auth";
import { apiClient, ApiError } from "@/lib/apiClient";
import type { Declaration, DeclarationHistoryOut, TaxPeriod } from "@/lib/types/declaration";
import type {
  ClientDocumentRequirement,
  DeclarationDocumentItem,
  DocumentItemStatus,
  DocumentRequirementCatalog,
  SyncRequirementsResult,
} from "@/lib/types/declarationDocument";
import type { TaxAssessment, TaxPayment } from "@/lib/types/taxAssessment";

const WORKFLOW_TARGETS: Record<string, string[]> = {
  PENDING: ["DOCUMENTS_REQUESTED", "NOT_APPLICABLE"],
  DOCUMENTS_REQUESTED: ["INFORMATION_INCOMPLETE", "DOCUMENTS_COMPLETE", "NOT_APPLICABLE"],
  INFORMATION_INCOMPLETE: ["DOCUMENTS_COMPLETE", "NOT_APPLICABLE"],
  DOCUMENTS_COMPLETE: ["IN_PROGRESS", "INFORMATION_INCOMPLETE", "NOT_APPLICABLE"],
  IN_PROGRESS: ["UNDER_REVIEW", "NOT_APPLICABLE"],
  UNDER_REVIEW: ["READY_TO_FILE", "IN_PROGRESS", "NOT_APPLICABLE"],
  READY_TO_FILE: ["UNDER_REVIEW", "NOT_APPLICABLE"],
};

type Tab = "resumen" | "documentos" | "impuestos" | "cierre" | "historial";

const TAB_LABELS: Record<Tab, string> = {
  resumen: "Resumen",
  documentos: "Documentos",
  impuestos: "Impuestos",
  cierre: "Cierre",
  historial: "Historial",
};

export default function DeclarationDetailPage() {
  const params = useParams<{ id: string }>();
  const [declaration, setDeclaration] = useState<Declaration | null>(null);
  const [history, setHistory] = useState<DeclarationHistoryOut | null>(null);
  const [taxPeriod, setTaxPeriod] = useState<TaxPeriod | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [tab, setTab] = useState<Tab>("resumen");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [documentItems, setDocumentItems] = useState<DeclarationDocumentItem[]>([]);
  const [requirements, setRequirements] = useState<ClientDocumentRequirement[]>([]);
  const [catalog, setCatalog] = useState<DocumentRequirementCatalog[]>([]);
  const [assessments, setAssessments] = useState<TaxAssessment[]>([]);
  const [payments, setPayments] = useState<TaxPayment[]>([]);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const [d, h, user] = await Promise.all([
        apiClient.get<Declaration>(`/declarations/${params.id}`),
        apiClient.get<DeclarationHistoryOut>(`/declarations/${params.id}/history`),
        fetchCurrentUser(),
      ]);
      setDeclaration(d);
      setHistory(h);
      setIsAdmin(user.role === "admin");

      const [period, items, reqs, cat, assess, pays] = await Promise.all([
        apiClient.get<TaxPeriod>(`/tax-periods/${d.tax_period_id}`),
        apiClient.get<DeclarationDocumentItem[]>(`/declarations/${d.id}/documents`),
        apiClient.get<ClientDocumentRequirement[]>(`/clients/${d.client_id}/document-requirements`),
        apiClient.get<DocumentRequirementCatalog[]>("/document-requirement-catalog"),
        apiClient.get<TaxAssessment[]>(`/declarations/${d.id}/tax-assessments`),
        apiClient.get<TaxPayment[]>(`/declarations/${d.id}/tax-payments`),
      ]);
      setTaxPeriod(period);
      setDocumentItems(items);
      setRequirements(reqs);
      setCatalog(cat);
      setAssessments(assess);
      setPayments(pays);
    } catch (err) {
      setLoadError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar la declaración.");
    }
  }, [params.id]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleWorkflowChange() {
    if (!declaration || !target) return;
    setError(null);
    let reason: string | null = null;
    const isRegression =
      (declaration.workflow_status === "DOCUMENTS_COMPLETE" && target === "INFORMATION_INCOMPLETE") ||
      (declaration.workflow_status === "UNDER_REVIEW" && target === "IN_PROGRESS") ||
      (declaration.workflow_status === "READY_TO_FILE" && target === "UNDER_REVIEW");
    if (isRegression) {
      reason = window.prompt("Esta es una regresión: indica el motivo");
      if (!reason) return;
    }
    try {
      await apiClient.patch(`/declarations/${declaration.id}/workflow-status`, {
        target,
        reason,
        row_version: declaration.row_version,
      });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "Error al cambiar el estado");
    }
  }

  async function handleSyncDocuments(): Promise<SyncRequirementsResult> {
    if (!declaration) throw new Error("no declaration");
    const result = await apiClient.post<SyncRequirementsResult>(
      `/declarations/${declaration.id}/documents/sync-requirements`,
    );
    await reload();
    return result;
  }

  async function handleChangeDocumentStatus(itemId: string, targetStatus: DocumentItemStatus, reason: string | null) {
    if (!declaration) return;
    await apiClient.patch(`/declarations/${declaration.id}/documents/${itemId}`, {
      target: targetStatus,
      reason,
      file_metadata: null,
    });
    await reload();
  }

  async function handleCreateAssessment(grossTaxAmount: string, creditsApplied: string, observations: string) {
    if (!declaration) return;
    await apiClient.post(`/declarations/${declaration.id}/tax-assessments`, {
      gross_tax_amount: grossTaxAmount,
      credits_applied: creditsApplied,
      observations: observations || null,
    });
    await reload();
  }

  async function handleCorrectAssessment(
    grossTaxAmount: string,
    creditsApplied: string,
    observations: string,
    reason: string,
  ) {
    if (!declaration) return;
    await apiClient.post(`/declarations/${declaration.id}/tax-assessments/correct`, {
      gross_tax_amount: grossTaxAmount,
      credits_applied: creditsApplied,
      observations: observations || null,
      reason,
    });
    await reload();
  }

  async function handleCreatePayment(payload: {
    idempotency_key: string;
    amount: string;
    paid_at: string;
    payment_method: string;
    operation_number: string | null;
    reference: string | null;
    observations: string | null;
    overpayment_confirmed: boolean;
    overpayment_reason: string | null;
  }) {
    if (!declaration) return;
    await apiClient.post(`/declarations/${declaration.id}/tax-payments`, payload);
    await reload();
  }

  async function handleVoidPayment(paymentId: string, reason: string) {
    if (!declaration) return;
    await apiClient.post(`/declarations/${declaration.id}/tax-payments/${paymentId}/void`, { reason });
    await reload();
  }

  async function handleClose(forceCloseWithPending: boolean, closeOverrideReason: string | null) {
    if (!declaration) return;
    await apiClient.post(`/declarations/${declaration.id}/close`, {
      force_close_with_pending: forceCloseWithPending,
      close_override_reason: closeOverrideReason,
    });
    await reload();
  }

  if (loadError) {
    return <p style={{ color: "var(--danger)" }}>{loadError}</p>;
  }

  if (!declaration || !history || !taxPeriod) {
    return <p>Cargando…</p>;
  }

  const availableTargets = WORKFLOW_TARGETS[declaration.workflow_status] ?? [];
  const canActOnDeclaration =
    taxPeriod.is_open && !["CLOSED", "CANCELLED", "NOT_APPLICABLE"].includes(declaration.workflow_status);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Declaración</h1>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <StatusSemaphore status={declaration.workflow_status} map={WORKFLOW_STATUS_SEMAPHORE} />
        <StatusSemaphore status={declaration.filing_status} map={FILING_STATUS_SEMAPHORE} />
        {declaration.computed_risk_status && (
          <StatusSemaphore status={declaration.computed_risk_status} map={RISK_STATUS_SEMAPHORE} />
        )}
      </div>

      <div
        role="tablist"
        aria-label="Secciones de la declaración"
        style={{ display: "flex", gap: "0.5rem", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}
      >
        {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            style={{
              padding: "0.5rem 1rem",
              border: "none",
              background: "none",
              fontWeight: tab === t ? 700 : 400,
              borderBottom: tab === t ? "2px solid var(--accent)" : "none",
              cursor: "pointer",
            }}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {tab === "resumen" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <dl>
            <dt>Vencimiento inicial</dt>
            <dd>{declaration.initial_effective_due_date}</dd>
            <dt>Vencimiento vigente</dt>
            <dd>{declaration.current_effective_due_date}</dd>
            <dt>Versión (row_version)</dt>
            <dd>{declaration.row_version}</dd>
          </dl>

          {!taxPeriod.is_open && (
            <p style={{ color: "var(--muted)" }}>
              El periodo tributario está cerrado: algunas acciones no están disponibles.
            </p>
          )}

          {availableTargets.length > 0 && (
            <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end" }}>
              <Select
                label="Cambiar estado de flujo"
                placeholder="Selecciona un estado"
                options={availableTargets.map((t) => ({ value: t, label: t }))}
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              />
              <Button type="button" onClick={handleWorkflowChange} disabled={!target}>
                Aplicar
              </Button>
            </div>
          )}
          {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
        </div>
      )}

      {tab === "documentos" && (
        <DocumentsPanel
          declaration={declaration}
          items={documentItems}
          requirements={requirements}
          catalog={catalog}
          periodIsOpen={taxPeriod.is_open}
          onSync={handleSyncDocuments}
          onChangeStatus={handleChangeDocumentStatus}
        />
      )}

      {tab === "impuestos" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
          <section>
            <h2 style={{ fontSize: "1.1rem" }}>Determinación tributaria</h2>
            <TaxAssessmentPanel
              assessments={assessments}
              canAct={canActOnDeclaration}
              onCreate={handleCreateAssessment}
              onCorrect={handleCorrectAssessment}
            />
          </section>
          <section>
            <h2 style={{ fontSize: "1.1rem" }}>Pagos tributarios</h2>
            <TaxPaymentsPanel
              taxPaymentStatus={declaration.tax_payment_status}
              payments={payments}
              assessments={assessments}
              canAct={canActOnDeclaration && assessments.some((a) => a.is_current)}
              onCreate={handleCreatePayment}
              onVoid={handleVoidPayment}
            />
          </section>
        </div>
      )}

      {tab === "cierre" && (
        <ClosePanel declaration={declaration} isAdmin={isAdmin} onClose={handleClose} />
      )}

      {tab === "historial" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          <section>
            <h2 style={{ fontSize: "1.1rem" }}>Estados</h2>
            <ul>
              {history.status_history.map((h) => (
                <li key={h.id}>
                  [{h.status_field}] {h.from_value ?? "—"} → {h.to_value} ({h.created_at})
                  {h.reason ? ` — ${h.reason}` : ""}
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2 style={{ fontSize: "1.1rem" }}>Vencimientos</h2>
            <ul>
              {history.due_date_history.map((h) => (
                <li key={h.id}>
                  {h.from_effective_due_date ?? "(generación)"} → {h.to_effective_due_date} ({h.created_at})
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}
