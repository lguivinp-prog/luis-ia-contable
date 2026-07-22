"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { ApiError, apiClient } from "@/lib/apiClient";
import type { Client, ClientListResponse } from "@/lib/types/client";
import type { TaxScheduleSource } from "@/lib/types/taxSchedule";
import type { PreviewDueDateResult } from "@/lib/types/taxSchedule";

export function DueDatePreviewForm({ sources }: { sources: TaxScheduleSource[] }) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Client[]>([]);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [taxPeriod, setTaxPeriod] = useState("");
  const [result, setResult] = useState<PreviewDueDateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const timeout = setTimeout(() => {
      if (!search.trim()) {
        setResults([]);
        return;
      }
      const params = new URLSearchParams({ search: search.trim(), limit: "10" });
      apiClient.get<ClientListResponse>(`/clients?${params.toString()}`).then((data) => setResults(data.items));
    }, 250);
    return () => clearTimeout(timeout);
  }, [search]);

  async function handlePreview() {
    if (!selectedClient || !sourceId || !taxPeriod.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const params = new URLSearchParams({
        source_id: sourceId,
        client_id: selectedClient.id,
        tax_period: taxPeriod.trim(),
      });
      const data = await apiClient.get<PreviewDueDateResult>(`/tax-schedules/preview-due-date?${params.toString()}`);
      setResult(data);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(typeof err.detail === "string" ? err.detail : "No se pudo resolver el vencimiento.");
      } else {
        setError("No se pudo resolver el vencimiento.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem", maxWidth: "640px" }}>
      <div style={{ position: "relative" }}>
        <Input
          label="Contribuyente (RUC o razón social)"
          name="client-search"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setSelectedClient(null);
            setResult(null);
          }}
        />
        {results.length > 0 && (
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: "0.25rem",
              position: "absolute",
              zIndex: 1,
              width: "100%",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "6px",
            }}
          >
            {results.map((client) => (
              <li key={client.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedClient(client);
                    setResults([]);
                    setSearch(client.business_name);
                  }}
                  style={{ width: "100%", textAlign: "left", padding: "0.4rem 0.5rem", background: "transparent", border: "none", cursor: "pointer" }}
                >
                  {client.ruc} — {client.business_name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <Select
        label="Fuente de cronograma"
        name="source_id"
        placeholder="Selecciona una fuente"
        options={sources.map((s) => ({ value: s.id, label: s.name }))}
        value={sourceId}
        onChange={(e) => setSourceId(e.target.value)}
      />

      <Input
        label="Periodo tributario (YYYY o YYYY-MM)"
        name="tax_period"
        placeholder="2026-01"
        value={taxPeriod}
        onChange={(e) => setTaxPeriod(e.target.value)}
      />

      <Button type="button" disabled={!selectedClient || !sourceId || !taxPeriod.trim() || loading} onClick={handlePreview}>
        {loading ? "Calculando…" : "Ver vencimiento"}
      </Button>

      {error && <div className="error-banner">{error}</div>}

      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", padding: "1rem", border: "1px solid var(--border)", borderRadius: "8px" }}>
          <p style={{ fontSize: "1.1rem", fontWeight: 700 }}>Vence: {result.due_date_effective}</p>
          <p>Regla aplicada: {result.rule_type === "exception" ? "Excepción" : "Entrada de cronograma"}</p>
          <p>Último dígito: {result.last_digit}</p>
          <p>Categoría exigida por la regla: {result.matched_category_code ?? "Ninguna (regla general)"}</p>
          {result.is_extension && result.due_date_original && (
            <p>Fecha original antes de prórroga: {result.due_date_original}</p>
          )}
          <p>Fuente: {result.source_name} · Versión: {result.version_label}</p>
          {result.warnings.length > 0 && (
            <ul>
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          <div className="simulated-banner">{result.demo_disclaimer}</div>
        </div>
      )}
    </div>
  );
}
