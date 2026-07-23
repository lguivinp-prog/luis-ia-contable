"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import {
  FILING_STATUS_SEMAPHORE,
  RISK_STATUS_SEMAPHORE,
  StatusSemaphore,
  WORKFLOW_STATUS_SEMAPHORE,
} from "@/components/ui/StatusSemaphore";
import { apiClient } from "@/lib/apiClient";
import type { DashboardOut, Declaration, TaxPeriod } from "@/lib/types/declaration";

const LIMIT = 20;

export default function DashboardPage() {
  const [periods, setPeriods] = useState<TaxPeriod[]>([]);
  const [selectedPeriodId, setSelectedPeriodId] = useState("");
  const [dashboard, setDashboard] = useState<DashboardOut | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void apiClient.get<TaxPeriod[]>("/tax-periods").then((data) => {
      setPeriods(data);
      if (data.length > 0) setSelectedPeriodId(data[0].id);
    });
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    const query = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
    if (selectedPeriodId) query.set("tax_period_id", selectedPeriodId);
    const data = await apiClient.get<DashboardOut>(`/dashboard/monthly?${query.toString()}`);
    setDashboard(data);
    setLoading(false);
  }, [selectedPeriodId, offset]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Tablero mensual</h1>

      <Select
        label="Periodo tributario"
        options={periods.map((p) => ({ value: p.id, label: p.display_name ?? p.canonical_code }))}
        value={selectedPeriodId}
        onChange={(e) => {
          setSelectedPeriodId(e.target.value);
          setOffset(0);
        }}
      />

      {loading || !dashboard ? (
        <p>Cargando…</p>
      ) : (
        <>
          <Table
            columns={[
              {
                key: "workflow",
                header: "Flujo",
                render: (d: Declaration) => <StatusSemaphore status={d.workflow_status} map={WORKFLOW_STATUS_SEMAPHORE} />,
              },
              {
                key: "filing",
                header: "Presentación",
                render: (d: Declaration) => <StatusSemaphore status={d.filing_status} map={FILING_STATUS_SEMAPHORE} />,
              },
              {
                key: "risk",
                header: "Riesgo",
                render: (d: Declaration) =>
                  d.computed_risk_status ? (
                    <StatusSemaphore status={d.computed_risk_status} map={RISK_STATUS_SEMAPHORE} />
                  ) : (
                    "—"
                  ),
              },
              {
                key: "due_date",
                header: "Vencimiento",
                render: (d: Declaration) => d.current_effective_due_date,
              },
              {
                key: "actions",
                header: "",
                render: (d: Declaration) => <Link href={`/declarations/${d.id}`}>Ver detalle</Link>,
              },
            ]}
            rows={dashboard.items}
            rowKey={(d) => d.id}
            emptyMessage="No hay declaraciones para este periodo."
          />
          <Pagination total={dashboard.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        </>
      )}
    </div>
  );
}
