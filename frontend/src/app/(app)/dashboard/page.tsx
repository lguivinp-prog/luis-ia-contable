"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import {
  DOCUMENT_STATUS_SEMAPHORE,
  FEE_COLLECTION_RISK_SEMAPHORE,
  FILING_STATUS_SEMAPHORE,
  RISK_STATUS_SEMAPHORE,
  StatusSemaphore,
  TAX_PAYMENT_STATUS_SEMAPHORE,
  WORKFLOW_STATUS_SEMAPHORE,
} from "@/components/ui/StatusSemaphore";
import { apiClient, ApiError } from "@/lib/apiClient";
import type { DashboardOut, DashboardRow, TaxPeriod } from "@/lib/types/declaration";

const LIMIT = 20;

const TAX_PAYMENT_STATUS_OPTIONS = [
  { value: "NOT_DETERMINED", label: "Sin determinar" },
  { value: "NO_TAX_DUE", label: "Sin impuesto" },
  { value: "CREDIT_BALANCE", label: "Saldo a favor" },
  { value: "PAYMENT_PENDING", label: "Pago pendiente" },
  { value: "PARTIALLY_PAID", label: "Pago parcial" },
  { value: "PAID", label: "Pagado" },
  { value: "NOT_APPLICABLE", label: "No aplica" },
];

const DOCUMENT_STATUS_OPTIONS = [
  { value: "NOT_CONFIGURED", label: "Sin configurar" },
  { value: "PENDING", label: "Pendiente" },
  { value: "PARTIAL", label: "Parcial" },
  { value: "OBSERVED", label: "Observado" },
  { value: "COMPLETE", label: "Completo" },
  { value: "NOT_APPLICABLE", label: "No aplica" },
];

export default function DashboardPage() {
  const [periods, setPeriods] = useState<TaxPeriod[]>([]);
  const [selectedPeriodId, setSelectedPeriodId] = useState("");
  const [taxPaymentStatusFilter, setTaxPaymentStatusFilter] = useState("");
  const [documentStatusFilter, setDocumentStatusFilter] = useState("");
  const [closedWithPendingFilter, setClosedWithPendingFilter] = useState("");
  const [dashboard, setDashboard] = useState<DashboardOut | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void apiClient.get<TaxPeriod[]>("/tax-periods").then((data) => {
      setPeriods(data);
      if (data.length > 0) setSelectedPeriodId(data[0].id);
    });
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const query = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
      if (selectedPeriodId) query.set("tax_period_id", selectedPeriodId);
      if (taxPaymentStatusFilter) query.set("tax_payment_status", taxPaymentStatusFilter);
      if (documentStatusFilter) query.set("document_status", documentStatusFilter);
      if (closedWithPendingFilter) query.set("closed_with_pending", closedWithPendingFilter);
      const data = await apiClient.get<DashboardOut>(`/dashboard/monthly?${query.toString()}`);
      setDashboard(data);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar el tablero.");
    } finally {
      setLoading(false);
    }
  }, [selectedPeriodId, taxPaymentStatusFilter, documentStatusFilter, closedWithPendingFilter, offset]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Tablero mensual</h1>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <Select
          label="Periodo tributario"
          options={periods.map((p) => ({ value: p.id, label: p.display_name ?? p.canonical_code }))}
          value={selectedPeriodId}
          onChange={(e) => {
            setSelectedPeriodId(e.target.value);
            setOffset(0);
          }}
        />
        <Select
          label="Estado tributario"
          placeholder="Todos"
          options={TAX_PAYMENT_STATUS_OPTIONS}
          value={taxPaymentStatusFilter}
          onChange={(e) => {
            setTaxPaymentStatusFilter(e.target.value);
            setOffset(0);
          }}
        />
        <Select
          label="Estado documental"
          placeholder="Todos"
          options={DOCUMENT_STATUS_OPTIONS}
          value={documentStatusFilter}
          onChange={(e) => {
            setDocumentStatusFilter(e.target.value);
            setOffset(0);
          }}
        />
        <Select
          label="Cierre con pendientes"
          placeholder="Todos"
          options={[
            { value: "true", label: "Solo cerradas con pendientes" },
            { value: "false", label: "Solo cerradas normalmente" },
          ]}
          value={closedWithPendingFilter}
          onChange={(e) => {
            setClosedWithPendingFilter(e.target.value);
            setOffset(0);
          }}
        />
      </div>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {loading || !dashboard ? (
        <p>Cargando…</p>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <Table
              columns={[
                {
                  key: "workflow",
                  header: "Flujo",
                  render: (d: DashboardRow) => (
                    <StatusSemaphore status={d.workflow_status} map={WORKFLOW_STATUS_SEMAPHORE} />
                  ),
                },
                {
                  key: "filing",
                  header: "Presentación",
                  render: (d: DashboardRow) => (
                    <StatusSemaphore status={d.filing_status} map={FILING_STATUS_SEMAPHORE} />
                  ),
                },
                {
                  key: "document_status",
                  header: "Documentos",
                  render: (d: DashboardRow) => (
                    <span style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
                      <StatusSemaphore status={d.document_status} map={DOCUMENT_STATUS_SEMAPHORE} />
                      {d.missing_documents_count > 0 && (
                        <span style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                          {d.missing_documents_count} faltante(s)
                        </span>
                      )}
                    </span>
                  ),
                },
                {
                  key: "tax_payment_status",
                  header: "Impuesto",
                  render: (d: DashboardRow) => (
                    <span style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
                      <StatusSemaphore status={d.tax_payment_status} map={TAX_PAYMENT_STATUS_SEMAPHORE} />
                      {d.tax_total_payable !== null && (
                        <span style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                          det. {d.tax_total_payable} / pagado {d.tax_total_paid}
                        </span>
                      )}
                    </span>
                  ),
                },
                {
                  key: "fees",
                  header: "Honorarios",
                  render: (d: DashboardRow) => (
                    <span style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
                      <span style={{ fontSize: "0.8rem" }}>
                        {d.fee_total_charges} cargo(s) · saldo {d.fee_balance}
                      </span>
                      {d.fee_worst_collection_risk && (
                        <StatusSemaphore
                          status={d.fee_worst_collection_risk}
                          map={FEE_COLLECTION_RISK_SEMAPHORE}
                        />
                      )}
                    </span>
                  ),
                },
                {
                  key: "risk",
                  header: "Riesgo",
                  render: (d: DashboardRow) =>
                    d.computed_risk_status ? (
                      <StatusSemaphore status={d.computed_risk_status} map={RISK_STATUS_SEMAPHORE} />
                    ) : (
                      "—"
                    ),
                },
                {
                  key: "closed_with_pending",
                  header: "Cierre",
                  render: (d: DashboardRow) =>
                    d.workflow_status === "CLOSED" ? (
                      <span className={`status-pill ${d.closed_with_pending ? "status-naranja" : "status-verde"}`}>
                        {d.closed_with_pending ? "Con pendientes" : "Normal"}
                      </span>
                    ) : (
                      "—"
                    ),
                },
                {
                  key: "due_date",
                  header: "Vencimiento",
                  render: (d: DashboardRow) => d.current_effective_due_date,
                },
                {
                  key: "actions",
                  header: "",
                  render: (d: DashboardRow) => <Link href={`/declarations/${d.id}`}>Ver detalle</Link>,
                },
              ]}
              rows={dashboard.items}
              rowKey={(d) => d.id}
              emptyMessage="No hay declaraciones para este periodo."
            />
          </div>
          <Pagination total={dashboard.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        </>
      )}
    </div>
  );
}
