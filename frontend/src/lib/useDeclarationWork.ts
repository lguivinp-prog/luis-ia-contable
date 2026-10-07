"use client";

// Datos y acciones de UNA declaracion (documentos, determinacion, pagos, presentacion, cierre). Lo usan la pagina de
// detalle de la declaracion y el workspace cliente/periodo del cockpit, para no duplicar ni bifurcar la logica.
import { useCallback, useEffect, useState } from "react";

import { ApiError, apiClient } from "@/lib/apiClient";
import type { Declaration, DeclarationHistoryOut, TaxPeriod } from "@/lib/types/declaration";
import type {
  ClientDocumentRequirement,
  DeclarationDocumentItem,
  DocumentItemStatus,
  DocumentRequirementCatalog,
  SyncRequirementsResult,
} from "@/lib/types/declarationDocument";
import type { TaxAssessment, TaxPayment } from "@/lib/types/taxAssessment";

export interface FilePayload {
  target: "READY" | "FILED" | "FAILED" | "NOT_REQUIRED";
  presentation_date: string | null;
  order_number: string | null;
  filing_reference: string | null;
  reason: string | null;
  row_version: number;
}

export interface PaymentPayload {
  idempotency_key: string;
  amount: string;
  paid_at: string;
  payment_method: string;
  operation_number: string | null;
  reference: string | null;
  observations: string | null;
  overpayment_confirmed: boolean;
  overpayment_reason: string | null;
}

export function useDeclarationWork(declarationId: string, onChanged?: () => void) {
  const [declaration, setDeclaration] = useState<Declaration | null>(null);
  const [history, setHistory] = useState<DeclarationHistoryOut | null>(null);
  const [taxPeriod, setTaxPeriod] = useState<TaxPeriod | null>(null);
  const [documentItems, setDocumentItems] = useState<DeclarationDocumentItem[]>([]);
  const [requirements, setRequirements] = useState<ClientDocumentRequirement[]>([]);
  const [catalog, setCatalog] = useState<DocumentRequirementCatalog[]>([]);
  const [assessments, setAssessments] = useState<TaxAssessment[]>([]);
  const [payments, setPayments] = useState<TaxPayment[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const [d, h] = await Promise.all([
        apiClient.get<Declaration>(`/declarations/${declarationId}`),
        apiClient.get<DeclarationHistoryOut>(`/declarations/${declarationId}/history`),
      ]);
      setDeclaration(d);
      setHistory(h);

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
  }, [declarationId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  // Tras cada cambio se recarga y se avisa al contenedor (p. ej. el workspace refresca su resumen).
  const refresh = useCallback(async () => {
    await reload();
    onChanged?.();
  }, [reload, onChanged]);

  async function syncDocuments(): Promise<SyncRequirementsResult> {
    const result = await apiClient.post<SyncRequirementsResult>(`/declarations/${declarationId}/documents/sync-requirements`);
    await refresh();
    return result;
  }

  async function changeDocumentStatus(itemId: string, target: DocumentItemStatus, reason: string | null) {
    await apiClient.patch(`/declarations/${declarationId}/documents/${itemId}`, { target, reason, file_metadata: null });
    await refresh();
  }

  async function createAssessment(grossTaxAmount: string, creditsApplied: string, observations: string) {
    await apiClient.post(`/declarations/${declarationId}/tax-assessments`, {
      gross_tax_amount: grossTaxAmount,
      credits_applied: creditsApplied,
      observations: observations || null,
    });
    await refresh();
  }

  async function correctAssessment(grossTaxAmount: string, creditsApplied: string, observations: string, reason: string) {
    await apiClient.post(`/declarations/${declarationId}/tax-assessments/correct`, {
      gross_tax_amount: grossTaxAmount,
      credits_applied: creditsApplied,
      observations: observations || null,
      reason,
    });
    await refresh();
  }

  async function createPayment(payload: PaymentPayload) {
    await apiClient.post(`/declarations/${declarationId}/tax-payments`, payload);
    await refresh();
  }

  async function voidPayment(paymentId: string, reason: string) {
    await apiClient.post(`/declarations/${declarationId}/tax-payments/${paymentId}/void`, { reason });
    await refresh();
  }

  async function fileDeclaration(payload: FilePayload) {
    await apiClient.patch(`/declarations/${declarationId}/filing`, payload);
    await refresh();
  }

  async function correctFiling(target: string, reason: string) {
    await apiClient.patch(`/declarations/${declarationId}/filing/correct`, { target, reason });
    await refresh();
  }

  async function closeDeclaration(forceCloseWithPending: boolean, closeOverrideReason: string | null) {
    await apiClient.post(`/declarations/${declarationId}/close`, {
      force_close_with_pending: forceCloseWithPending,
      close_override_reason: closeOverrideReason,
    });
    await refresh();
  }

  return {
    declaration,
    history,
    taxPeriod,
    documentItems,
    requirements,
    catalog,
    assessments,
    payments,
    loadError,
    reload: refresh,
    syncDocuments,
    changeDocumentStatus,
    createAssessment,
    correctAssessment,
    createPayment,
    voidPayment,
    fileDeclaration,
    correctFiling,
    closeDeclaration,
  };
}

export type DeclarationWork = ReturnType<typeof useDeclarationWork>;
