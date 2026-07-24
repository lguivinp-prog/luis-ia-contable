"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { FILING_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { ApiError } from "@/lib/apiClient";
import type { Declaration, DeclarationHistoryOut } from "@/lib/types/declaration";

const CORRECTION_TARGETS = [
  { value: "NOT_FILED", label: "No presentada" },
  { value: "READY", label: "Lista" },
  { value: "FILED", label: "Presentada" },
  { value: "FAILED", label: "Fallida" },
  { value: "NOT_REQUIRED", label: "No requerida" },
];

interface PresentationPanelProps {
  declaration: Declaration;
  filingHistory: DeclarationHistoryOut["filing_history"];
  canAct: boolean;
  onFile: (payload: {
    target: "READY" | "FILED" | "FAILED" | "NOT_REQUIRED";
    presentation_date: string | null;
    order_number: string | null;
    filing_reference: string | null;
    reason: string | null;
    row_version: number;
  }) => Promise<void>;
  onCorrect: (target: string, reason: string) => Promise<void>;
}

export function PresentationPanel({ declaration, filingHistory, canAct, onFile, onCorrect }: PresentationPanelProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [presentationDate, setPresentationDate] = useState(new Date().toISOString().slice(0, 10));
  const [orderNumber, setOrderNumber] = useState("");
  const [filingReference, setFilingReference] = useState("");

  const [failReason, setFailReason] = useState("");
  const [showFailForm, setShowFailForm] = useState(false);

  const [showCorrection, setShowCorrection] = useState(false);
  const [correctionTarget, setCorrectionTarget] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");

  async function runAction(fn: () => Promise<void>, fallbackMessage: string) {
    setSubmitting(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(
          "La transición ya no es válida (el estado cambió mientras trabajabas, o hay un conflicto de " +
            "versión). Recarga la declaración y vuelve a intentar.",
        );
      } else if (err instanceof ApiError && err.status === 422) {
        setError(err.detail ? String(err.detail) : "Faltan datos requeridos para esta acción.");
      } else {
        setError(err instanceof ApiError ? String(err.detail) : fallbackMessage);
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handlePrepare() {
    const confirmed = window.confirm("¿Confirmas que esta declaración está lista para presentarse?");
    if (!confirmed) return;
    await runAction(
      () =>
        onFile({
          target: "READY",
          presentation_date: null,
          order_number: null,
          filing_reference: null,
          reason: null,
          row_version: declaration.row_version,
        }),
      "No se pudo preparar la declaración para presentación.",
    );
  }

  async function handleMarkNotRequired() {
    const confirmed = window.confirm(
      "¿Confirmas que la presentación NO es requerida para esta declaración? Esta acción es terminal.",
    );
    if (!confirmed) return;
    await runAction(
      () =>
        onFile({
          target: "NOT_REQUIRED",
          presentation_date: null,
          order_number: null,
          filing_reference: null,
          reason: null,
          row_version: declaration.row_version,
        }),
      "No se pudo marcar la presentación como no requerida.",
    );
  }

  async function handleFile() {
    if (!presentationDate) {
      setError("La fecha de presentación es obligatoria para registrar FILED.");
      return;
    }
    const confirmed = window.confirm(
      `¿Confirmas registrar la presentación con fecha ${presentationDate}? Esta acción queda auditada.`,
    );
    if (!confirmed) return;
    await runAction(
      () =>
        onFile({
          target: "FILED",
          presentation_date: presentationDate,
          order_number: orderNumber || null,
          filing_reference: filingReference || null,
          reason: null,
          row_version: declaration.row_version,
        }),
      "No se pudo registrar la presentación.",
    );
  }

  async function handleFail() {
    if (!failReason.trim()) {
      setError("Registrar un fallo de presentación requiere una observación explícita.");
      return;
    }
    await runAction(
      () =>
        onFile({
          target: "FAILED",
          presentation_date: null,
          order_number: null,
          filing_reference: null,
          reason: failReason,
          row_version: declaration.row_version,
        }),
      "No se pudo registrar el fallo de presentación.",
    );
    setShowFailForm(false);
    setFailReason("");
  }

  async function handleRetry() {
    const confirmed = window.confirm("¿Confirmas volver a intentar la presentación (FAILED → Lista)?");
    if (!confirmed) return;
    await runAction(
      () =>
        onFile({
          target: "READY",
          presentation_date: null,
          order_number: null,
          filing_reference: null,
          reason: null,
          row_version: declaration.row_version,
        }),
      "No se pudo reintentar la presentación.",
    );
  }

  async function handleCorrection() {
    if (!correctionTarget) {
      setError("Selecciona el estado corregido.");
      return;
    }
    if (!correctionReason.trim()) {
      setError("La corrección administrativa requiere un motivo explícito.");
      return;
    }
    const confirmed = window.confirm(
      "Vas a corregir administrativamente el estado de presentación de una declaración FILED. " +
        "Esta es una acción excepcional y auditada. ¿Confirmas que cuentas con autorización para hacerlo?",
    );
    if (!confirmed) return;
    await runAction(() => onCorrect(correctionTarget, correctionReason), "No se pudo aplicar la corrección administrativa.");
    setShowCorrection(false);
    setCorrectionTarget("");
    setCorrectionReason("");
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <strong>Estado de presentación:</strong>
          <StatusSemaphore status={declaration.filing_status} map={FILING_STATUS_SEMAPHORE} />
        </div>
        <dl>
          <dt>Fecha de presentación</dt>
          <dd>{declaration.presentation_date ?? "—"}</dd>
          <dt>Número de orden</dt>
          <dd>{declaration.order_number ?? "—"}</dd>
          <dt>Referencia de presentación</dt>
          <dd>{declaration.filing_reference ?? "—"}</dd>
        </dl>
      </div>

      {!canAct && (
        <p style={{ color: "var(--muted)" }}>
          El periodo o la declaración están cerrados: no se admiten acciones de presentación.
        </p>
      )}

      {canAct && declaration.filing_status === "NOT_FILED" && (
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
          <Button type="button" onClick={handlePrepare} disabled={submitting}>
            {submitting ? "Procesando…" : "Preparar para presentación"}
          </Button>
          <Button type="button" variant="secondary" onClick={handleMarkNotRequired} disabled={submitting}>
            Marcar como no requerida
          </Button>
        </div>
      )}

      {canAct && declaration.filing_status === "READY" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", maxWidth: "360px" }}>
            <h3 style={{ fontSize: "1rem" }}>Registrar presentación</h3>
            <Input
              label="Fecha de presentación"
              type="date"
              required
              value={presentationDate}
              onChange={(e) => setPresentationDate(e.target.value)}
            />
            <Input
              label="Número de orden (opcional)"
              value={orderNumber}
              onChange={(e) => setOrderNumber(e.target.value)}
            />
            <Input
              label="Referencia de presentación (opcional)"
              value={filingReference}
              onChange={(e) => setFilingReference(e.target.value)}
            />
            <Button type="button" onClick={handleFile} disabled={submitting || !presentationDate}>
              {submitting ? "Registrando…" : "Registrar presentación"}
            </Button>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", maxWidth: "360px" }}>
            {!showFailForm ? (
              <Button type="button" variant="danger" onClick={() => setShowFailForm(true)} disabled={submitting}>
                Registrar fallo
              </Button>
            ) : (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.5rem",
                  padding: "0.75rem",
                  border: "1px solid var(--danger)",
                  borderRadius: "6px",
                }}
              >
                <Input
                  label="Motivo u observación del fallo"
                  required
                  value={failReason}
                  onChange={(e) => setFailReason(e.target.value)}
                />
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <Button type="button" variant="danger" onClick={handleFail} disabled={submitting}>
                    {submitting ? "Registrando…" : "Confirmar fallo"}
                  </Button>
                  <Button type="button" variant="secondary" onClick={() => setShowFailForm(false)}>
                    Cancelar
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {canAct && declaration.filing_status === "FAILED" && (
        <Button type="button" onClick={handleRetry} disabled={submitting}>
          {submitting ? "Procesando…" : "Reintentar presentación (volver a Lista)"}
        </Button>
      )}

      {declaration.filing_status === "NOT_REQUIRED" && (
        <p style={{ color: "var(--muted)" }}>
          La presentación fue marcada como no requerida — este estado es terminal.
        </p>
      )}

      {declaration.filing_status === "FILED" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <p style={{ color: "var(--muted)" }}>
            Esta declaración ya fue presentada. Para corregir un error, usa exclusivamente la corrección
            administrativa (nunca un cambio directo de estado).
          </p>
          {canAct && !showCorrection && (
            <Button type="button" variant="danger" onClick={() => setShowCorrection(true)}>
              Corrección administrativa
            </Button>
          )}
          {canAct && showCorrection && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "0.5rem",
                maxWidth: "360px",
                padding: "0.75rem",
                border: "1px solid var(--danger)",
                borderRadius: "6px",
              }}
            >
              <p style={{ color: "var(--danger)", fontWeight: 700 }}>
                Corrección administrativa -- requiere motivo y queda auditada.
              </p>
              <Select
                label="Estado corregido"
                placeholder="Selecciona…"
                options={CORRECTION_TARGETS.filter((t) => t.value !== "FILED")}
                value={correctionTarget}
                onChange={(e) => setCorrectionTarget(e.target.value)}
              />
              <Input
                label="Motivo de la corrección"
                required
                value={correctionReason}
                onChange={(e) => setCorrectionReason(e.target.value)}
              />
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <Button
                  type="button"
                  variant="danger"
                  onClick={handleCorrection}
                  disabled={submitting || !correctionTarget || !correctionReason.trim()}
                >
                  {submitting ? "Aplicando…" : "Confirmar corrección"}
                </Button>
                <Button type="button" variant="secondary" onClick={() => setShowCorrection(false)}>
                  Cancelar
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      <div>
        <h3 style={{ fontSize: "1rem" }}>Historial de presentación</h3>
        {filingHistory.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>Todavía no hay cambios de presentación registrados.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Fecha</th>
                <th>De</th>
                <th>A</th>
                <th>Fecha de presentación</th>
                <th>Orden</th>
                <th>Referencia</th>
                <th>Origen</th>
                <th>Motivo</th>
              </tr>
            </thead>
            <tbody>
              {filingHistory.map((h) => (
                <tr key={h.id}>
                  <td>{h.created_at}</td>
                  <td>{h.from_filing_status ?? "(generación)"}</td>
                  <td>{h.to_filing_status}</td>
                  <td>{h.presentation_date ?? "—"}</td>
                  <td>{h.order_number ?? "—"}</td>
                  <td>{h.filing_reference ?? "—"}</td>
                  <td>{h.origin}</td>
                  <td>{h.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
