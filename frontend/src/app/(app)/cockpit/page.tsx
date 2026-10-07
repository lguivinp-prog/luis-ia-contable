"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Input } from "@/components/ui/Input";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import {
  DOCUMENT_STATUS_SEMAPHORE,
  StatusSemaphore,
  TAX_PAYMENT_STATUS_SEMAPHORE,
} from "@/components/ui/StatusSemaphore";
import { ApiError, apiClient } from "@/lib/apiClient";
import { fetchMonthlyCockpit } from "@/lib/cockpit";
import {
  ACCOUNTING_STATUS_SEMAPHORE,
  BUZON_STATUS_SEMAPHORE,
  COCKPIT_RISK_SEMAPHORE,
  COMMUNICATION_STATUS_SEMAPHORE,
  DOCUMENT_FILTER_OPTIONS,
  FILING_PROGRESS_FILTER_OPTIONS,
  MONTHLY_PHASE_SEMAPHORE,
  RISK_FILTER_OPTIONS,
  SIRE_FILTER_OPTIONS,
  SIRE_STATUS_SEMAPHORE,
  buildSummaryCards,
  declarationsLabel,
  defaultPeriodId,
  feeLabel,
  formatDate,
  isPeriodNotGenerated,
  monthlyPeriodsNewestFirst,
  rowActions,
  taxLabel,
} from "@/lib/cockpitPresentation";
import type { CockpitOut, CockpitRow } from "@/lib/types/cockpit";
import type { TaxPeriod } from "@/lib/types/declaration";

const LIMIT = 25;
const MUTED = { fontSize: "0.78rem", color: "var(--muted)" } as const;

export default function CockpitPage() {
  const [periods, setPeriods] = useState<TaxPeriod[]>([]);
  const [periodId, setPeriodId] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [risk, setRisk] = useState("");
  const [sire, setSire] = useState("");
  const [documents, setDocuments] = useState("");
  const [filing, setFiling] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<CockpitOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void apiClient
      .get<TaxPeriod[]>("/tax-periods")
      .then((list) => {
        setPeriods(list);
        const initial = defaultPeriodId(list);
        if (initial) setPeriodId(initial);
        else setLoading(false);
      })
      .catch(() => {
        setError("No se pudieron cargar los periodos tributarios.");
        setLoading(false);
      });
  }, []);

  // La busqueda se aplica con una pequena espera para no consultar en cada tecla.
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setOffset(0);
    }, 350);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const reload = useCallback(async () => {
    if (!periodId) return;
    setLoading(true);
    setError(null);
    try {
      setData(
        await fetchMonthlyCockpit({
          tax_period_id: periodId,
          search,
          overall_risk: risk,
          sire_status: sire,
          document_status: documents,
          filing_progress: filing,
          limit: LIMIT,
          offset,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? "No se pudo cargar el cockpit." : "No se pudo cargar el cockpit.");
    } finally {
      setLoading(false);
    }
  }, [periodId, search, risk, sire, documents, filing, offset]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  const monthly = useMemo(() => monthlyPeriodsNewestFirst(periods), [periods]);
  const cards = data ? buildSummaryCards(data.summary) : [];
  const resetPage = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value);
    setOffset(0);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      <div>
        <h1 style={{ fontSize: "1.4rem" }}>Cockpit mensual</h1>
        <p style={{ color: "var(--muted)", marginTop: "0.3rem" }}>
          Una fila por cliente activo y periodo. Los estados derivados se calculan a partir de los datos existentes; Buzón, Contabilidad y Comunicación son manuales y se registran en el workspace del cliente.
        </p>
      </div>

      <div style={{ display: "flex", gap: "0.9rem", flexWrap: "wrap", alignItems: "flex-end" }}>
        <Select
          id="cockpit-period"
          label="Periodo tributario"
          options={monthly.map((p) => ({ value: p.id, label: p.display_name ?? p.canonical_code }))}
          value={periodId}
          onChange={(e) => {
            setPeriodId(e.target.value);
            setOffset(0);
          }}
        />
        <Input
          id="cockpit-search"
          label="Buscar cliente o RUC"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        <Select
          id="cockpit-risk"
          label="Riesgo"
          placeholder="Todos"
          options={RISK_FILTER_OPTIONS}
          value={risk}
          onChange={(e) => resetPage(setRisk)(e.target.value)}
        />
        <Select
          id="cockpit-sire"
          label="SIRE"
          placeholder="Todos"
          options={SIRE_FILTER_OPTIONS}
          value={sire}
          onChange={(e) => resetPage(setSire)(e.target.value)}
        />
        <Select
          id="cockpit-documents"
          label="Documentos"
          placeholder="Todos"
          options={DOCUMENT_FILTER_OPTIONS}
          value={documents}
          onChange={(e) => resetPage(setDocuments)(e.target.value)}
        />
        <Select
          id="cockpit-filing"
          label="Declaraciones"
          placeholder="Todas"
          options={FILING_PROGRESS_FILTER_OPTIONS}
          value={filing}
          onChange={(e) => resetPage(setFiling)(e.target.value)}
        />
      </div>

      {error && (
        <p role="alert" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}

      {data && (
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }} aria-label="Resumen del periodo">
          {cards.map((card) => (
            <div
              key={card.key}
              style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "0.6rem 0.9rem", minWidth: "128px" }}
            >
              <div style={{ fontSize: "1.45rem", fontWeight: 700 }}>{card.value}</div>
              <div style={MUTED}>{card.label}</div>
            </div>
          ))}
        </div>
      )}

      {!data && loading && <p>Cargando…</p>}
      {data && (
        <>
          {data.items.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>No hay clientes que coincidan con los filtros.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="data-table" aria-label="Clientes del periodo">
                <thead>
                  <tr>
                    {["Cliente", "RUC", "Vence", "Fase", "Documentos", "SIRE", "Declaraciones", "Impuesto", "Honorarios", "Buzón", "Contabilidad", "Comunicación", "Riesgo", "Acción"].map(
                      (header) => (
                        <th key={header}>{header}</th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <CockpitTableRow key={row.client_id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pagination total={data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        </>
      )}
    </div>
  );
}

function CockpitTableRow({ row }: { row: CockpitRow }) {
  const notGenerated = isPeriodNotGenerated(row);
  const tax = taxLabel(row);
  const fee = feeLabel(row);
  return (
    <tr>
      <td>
        <strong>{row.business_name}</strong>
      </td>
      <td>
        {row.ruc}
        <div style={MUTED}>Dígito {row.ruc_last_digit}</div>
      </td>
      <td>{formatDate(row.nearest_due_date)}</td>
      <td>
        <StatusSemaphore status={row.monthly_phase} map={MONTHLY_PHASE_SEMAPHORE} />
      </td>
      <td>
        {row.document_status ? (
          <>
            <StatusSemaphore status={row.document_status} map={DOCUMENT_STATUS_SEMAPHORE} />
            {row.missing_documents_count > 0 && <div style={MUTED}>{row.missing_documents_count} faltantes</div>}
          </>
        ) : (
          "—"
        )}
      </td>
      <td>{row.sire_status ? <StatusSemaphore status={row.sire_status} map={SIRE_STATUS_SEMAPHORE} /> : "—"}</td>
      <td>
        {notGenerated ? <StatusSemaphore status="PERIOD_NOT_GENERATED" map={COCKPIT_RISK_SEMAPHORE} /> : declarationsLabel(row)}
      </td>
      <td>
        {row.tax_payment_status ? (
          <>
            <StatusSemaphore status={row.tax_payment_status} map={TAX_PAYMENT_STATUS_SEMAPHORE} />
            {tax && <div style={MUTED}>{tax}</div>}
          </>
        ) : (
          "—"
        )}
      </td>
      <td>{fee ?? "—"}</td>
      <td>
        <StatusSemaphore status={row.buzon_status} map={BUZON_STATUS_SEMAPHORE} />
      </td>
      <td>
        <StatusSemaphore status={row.accounting_status} map={ACCOUNTING_STATUS_SEMAPHORE} />
      </td>
      <td>
        <StatusSemaphore status={row.client_communication_status} map={COMMUNICATION_STATUS_SEMAPHORE} />
      </td>
      <td>
        <StatusSemaphore status={row.overall_risk} map={COCKPIT_RISK_SEMAPHORE} />
      </td>
      <td>
        <div style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
          {rowActions(row).map((action) => (
            <Link key={action.href} href={action.href}>
              {action.label}
            </Link>
          ))}
        </div>
      </td>
    </tr>
  );
}
