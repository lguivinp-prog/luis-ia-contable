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
import { apiClient, ApiError } from "@/lib/apiClient";
import type { Declaration, DeclarationHistoryOut } from "@/lib/types/declaration";

const WORKFLOW_TARGETS: Record<string, string[]> = {
  PENDING: ["DOCUMENTS_REQUESTED", "NOT_APPLICABLE"],
  DOCUMENTS_REQUESTED: ["INFORMATION_INCOMPLETE", "DOCUMENTS_COMPLETE", "NOT_APPLICABLE"],
  INFORMATION_INCOMPLETE: ["DOCUMENTS_COMPLETE", "NOT_APPLICABLE"],
  DOCUMENTS_COMPLETE: ["IN_PROGRESS", "INFORMATION_INCOMPLETE", "NOT_APPLICABLE"],
  IN_PROGRESS: ["UNDER_REVIEW", "NOT_APPLICABLE"],
  UNDER_REVIEW: ["READY_TO_FILE", "IN_PROGRESS", "NOT_APPLICABLE"],
  READY_TO_FILE: ["UNDER_REVIEW", "NOT_APPLICABLE"],
};

type Tab = "resumen" | "historial";

export default function DeclarationDetailPage() {
  const params = useParams<{ id: string }>();
  const [declaration, setDeclaration] = useState<Declaration | null>(null);
  const [history, setHistory] = useState<DeclarationHistoryOut | null>(null);
  const [tab, setTab] = useState<Tab>("resumen");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [d, h] = await Promise.all([
      apiClient.get<Declaration>(`/declarations/${params.id}`),
      apiClient.get<DeclarationHistoryOut>(`/declarations/${params.id}/history`),
    ]);
    setDeclaration(d);
    setHistory(h);
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

  if (!declaration || !history) {
    return <p>Cargando…</p>;
  }

  const availableTargets = WORKFLOW_TARGETS[declaration.workflow_status] ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Declaración</h1>

      <div style={{ display: "flex", gap: "1rem" }}>
        <StatusSemaphore status={declaration.workflow_status} map={WORKFLOW_STATUS_SEMAPHORE} />
        <StatusSemaphore status={declaration.filing_status} map={FILING_STATUS_SEMAPHORE} />
        {declaration.computed_risk_status && (
          <StatusSemaphore status={declaration.computed_risk_status} map={RISK_STATUS_SEMAPHORE} />
        )}
      </div>

      <div style={{ display: "flex", gap: "0.5rem", borderBottom: "1px solid var(--border)" }}>
        {(["resumen", "historial"] as Tab[]).map((t) => (
          <button
            key={t}
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
            {t === "resumen" ? "Resumen" : "Historial"}
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
