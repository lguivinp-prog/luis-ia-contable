"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import { TAX_PERIOD_TYPE_LABEL } from "@/components/ui/StatusSemaphore";
import { apiClient, ApiError } from "@/lib/apiClient";
import type { TaxPeriod, TaxPeriodType } from "@/lib/types/declaration";

const PERIOD_TYPE_OPTIONS = [
  { value: "MONTHLY", label: "Mensual" },
  { value: "ANNUAL", label: "Anual" },
  { value: "SPECIAL", label: "Especial" },
];

export default function TaxPeriodsPage() {
  const [periods, setPeriods] = useState<TaxPeriod[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [periodType, setPeriodType] = useState<TaxPeriodType>("MONTHLY");
  const [fiscalYear, setFiscalYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(1);
  const [canonicalCode, setCanonicalCode] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    const data = await apiClient.get<TaxPeriod[]>("/tax-periods");
    setPeriods(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await apiClient.post("/tax-periods", {
        period_type: periodType,
        fiscal_year: fiscalYear,
        month: periodType === "MONTHLY" ? month : null,
        canonical_code: periodType === "SPECIAL" ? canonicalCode : null,
        schedule_tax_period_code: null,
        display_name: displayName || null,
        start_date: periodType === "SPECIAL" ? startDate || null : null,
        end_date: periodType === "SPECIAL" ? endDate || null : null,
        description: null,
      });
      setCanonicalCode("");
      setDisplayName("");
      setStartDate("");
      setEndDate("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "Error al crear el periodo");
    }
  }

  async function handleReopen(period: TaxPeriod) {
    const reason = window.prompt("Motivo de la reapertura:");
    if (!reason) return;
    await apiClient.patch(`/tax-periods/${period.id}/reopen`, { reason });
    await reload();
  }

  async function handleClose(period: TaxPeriod) {
    await apiClient.patch(`/tax-periods/${period.id}/close`, { reason: null });
    await reload();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Periodos tributarios</h1>

      <form
        onSubmit={handleCreate}
        style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}
      >
        <Select
          label="Tipo"
          options={PERIOD_TYPE_OPTIONS}
          value={periodType}
          onChange={(e) => setPeriodType(e.target.value as TaxPeriodType)}
        />
        <Input
          label="Año fiscal"
          type="number"
          value={fiscalYear}
          onChange={(e) => setFiscalYear(Number(e.target.value))}
        />
        {periodType === "MONTHLY" && (
          <Input
            label="Mes"
            type="number"
            min={1}
            max={12}
            value={month}
            onChange={(e) => setMonth(Number(e.target.value))}
          />
        )}
        {periodType === "SPECIAL" && (
          <>
            <Input
              label="Código canónico"
              value={canonicalCode}
              onChange={(e) => setCanonicalCode(e.target.value)}
              required
            />
            <Input label="Inicio" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} required />
            <Input label="Fin" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required />
          </>
        )}
        <Input label="Nombre (opcional)" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <Button type="submit">Crear periodo</Button>
      </form>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {loading ? (
        <p>Cargando…</p>
      ) : (
        <Table
          columns={[
            { key: "type", header: "Tipo", render: (p: TaxPeriod) => TAX_PERIOD_TYPE_LABEL[p.period_type] },
            { key: "code", header: "Código", render: (p: TaxPeriod) => p.display_name ?? p.canonical_code },
            { key: "range", header: "Rango", render: (p: TaxPeriod) => `${p.start_date} → ${p.end_date}` },
            {
              key: "status",
              header: "Estado",
              render: (p: TaxPeriod) => (p.is_open ? "Abierto" : "Cerrado"),
            },
            {
              key: "actions",
              header: "",
              render: (p: TaxPeriod) =>
                p.is_open ? (
                  <Button type="button" variant="secondary" onClick={() => handleClose(p)}>
                    Cerrar
                  </Button>
                ) : (
                  <Button type="button" variant="secondary" onClick={() => handleReopen(p)}>
                    Reabrir
                  </Button>
                ),
            },
          ]}
          rows={periods}
          rowKey={(p) => p.id}
          emptyMessage="Todavía no hay periodos tributarios creados."
        />
      )}
    </div>
  );
}
