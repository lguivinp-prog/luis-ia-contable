"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import { GENERATION_RUN_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { apiClient, ApiError } from "@/lib/apiClient";
import type { DeclarationGenerationRun, DeclarationGenerationRunItem, TaxPeriod } from "@/lib/types/declaration";

const CLASSIFICATION_LABEL: Record<string, string> = {
  APTO: "Apto",
  YA_EXISTENTE: "Ya existente",
  CLIENTE_INACTIVO: "Cliente inactivo",
  OBLIGACION_INACTIVA: "Obligación inactiva",
  SIN_CRONOGRAMA: "Sin cronograma aprobado",
  AMBIGUO: "Regla ambigua",
  SIN_REGLA_APLICABLE: "Sin regla aplicable",
  ERROR_INESPERADO: "Error inesperado",
};

export default function GenerateDeclarationsPage() {
  const [periods, setPeriods] = useState<TaxPeriod[]>([]);
  const [selectedPeriodId, setSelectedPeriodId] = useState("");
  const [run, setRun] = useState<DeclarationGenerationRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void apiClient.get<TaxPeriod[]>("/tax-periods").then((data) => {
      setPeriods(data.filter((p) => p.is_open));
      if (data.length > 0) setSelectedPeriodId(data[0].id);
    });
  }, []);

  const reloadRun = useCallback(async (runId: string) => {
    const data = await apiClient.get<DeclarationGenerationRun>(`/declarations/generation-runs/${runId}`);
    setRun(data);
  }, []);

  async function handlePreview() {
    if (!selectedPeriodId) return;
    setBusy(true);
    setError(null);
    try {
      const data = await apiClient.post<DeclarationGenerationRun>("/declarations/generation-runs/preview", {
        tax_period_id: selectedPeriodId,
        filters: null,
      });
      setRun(data);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "Error al previsualizar");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleExclude(item: DeclarationGenerationRunItem) {
    if (!run) return;
    await apiClient.patch(`/declarations/generation-runs/${run.id}/items/${item.id}`, {
      excluded_by_user: !item.excluded_by_user,
    });
    await reloadRun(run.id);
  }

  async function handleConfirm() {
    if (!run) return;
    setBusy(true);
    setError(null);
    try {
      const data = await apiClient.post<DeclarationGenerationRun>(
        `/declarations/generation-runs/${run.id}/confirm`
      );
      setRun(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // El backend ya marco el run como STALE; se recarga para ver el detalle.
        await reloadRun(run.id);
        setError("El preview quedó desactualizado. Actualiza el preview y confirma de nuevo.");
      } else {
        setError(err instanceof ApiError ? String(err.detail) : "Error al confirmar");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshPreview() {
    if (!run) return;
    setBusy(true);
    setError(null);
    try {
      const data = await apiClient.post<DeclarationGenerationRun>(
        `/declarations/generation-runs/${run.id}/refresh-preview`
      );
      setRun(data);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Generación masiva de declaraciones</h1>

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end" }}>
        <Select
          label="Periodo tributario"
          options={periods.map((p) => ({ value: p.id, label: p.display_name ?? p.canonical_code }))}
          value={selectedPeriodId}
          onChange={(e) => setSelectedPeriodId(e.target.value)}
        />
        <Button type="button" onClick={handlePreview} disabled={busy || !selectedPeriodId}>
          Previsualizar
        </Button>
      </div>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {run && (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
            <StatusSemaphore status={run.status} map={GENERATION_RUN_STATUS_SEMAPHORE} />
            <span>
              Analizados: {run.total_analyzed} · Creados: {run.total_created} · Omitidos: {run.total_skipped} ·
              Fallidos: {run.total_failed}
            </span>
          </div>

          {run.status === "STALE" && (
            <Button type="button" variant="secondary" onClick={handleRefreshPreview} disabled={busy}>
              Actualizar preview
            </Button>
          )}

          <Table
            columns={[
              {
                key: "classification",
                header: "Clasificación",
                render: (i: DeclarationGenerationRunItem) =>
                  CLASSIFICATION_LABEL[i.classification] ?? i.classification,
              },
              {
                key: "due_date",
                header: "Vencimiento previsto",
                render: (i: DeclarationGenerationRunItem) => i.preview_effective_due_date ?? "—",
              },
              {
                key: "excluded",
                header: "Excluido",
                render: (i: DeclarationGenerationRunItem) => (i.excluded_by_user ? "Sí" : "No"),
              },
              {
                key: "actions",
                header: "",
                render: (i: DeclarationGenerationRunItem) =>
                  i.classification === "APTO" && run.status === "PREVIEWED" ? (
                    <Button type="button" variant="secondary" onClick={() => handleToggleExclude(i)}>
                      {i.excluded_by_user ? "Incluir" : "Excluir"}
                    </Button>
                  ) : null,
              },
            ]}
            rows={run.items}
            rowKey={(i) => i.id}
          />

          {(run.status === "PREVIEWED" || run.status === "FAILED") && (
            <Button type="button" onClick={handleConfirm} disabled={busy}>
              Confirmar generación
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
