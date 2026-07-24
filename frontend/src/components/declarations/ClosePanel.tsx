"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ApiError } from "@/lib/apiClient";
import type { Declaration } from "@/lib/types/declaration";

const FILING_ELIGIBLE = new Set(["FILED", "NOT_REQUIRED"]);
const TAX_ELIGIBLE = new Set(["PAID", "NO_TAX_DUE", "CREDIT_BALANCE", "NOT_APPLICABLE"]);
const DOCUMENT_ELIGIBLE = new Set(["COMPLETE", "NOT_APPLICABLE"]);

interface ClosePanelProps {
  declaration: Declaration;
  canClose: boolean;
  canOverrideClose: boolean;
  onClose: (forceCloseWithPending: boolean, closeOverrideReason: string | null) => Promise<void>;
}

function PreconditionRow({ label, met }: { label: string; met: boolean }) {
  return (
    <li style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
      <span aria-hidden="true">{met ? "✅" : "⏳"}</span>
      <span className={`status-pill ${met ? "status-verde" : "status-amarillo"}`}>
        {label}: {met ? "cumplida" : "pendiente"}
      </span>
    </li>
  );
}

export function ClosePanel({ declaration, canClose, canOverrideClose, onClose }: ClosePanelProps) {
  const [showOverride, setShowOverride] = useState(false);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (declaration.workflow_status === "CLOSED") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        <span className={`status-pill ${declaration.closed_with_pending ? "status-naranja" : "status-verde"}`}>
          Declaración cerrada{declaration.closed_with_pending ? " con pendientes" : ""}
        </span>
        <p style={{ color: "var(--muted)" }}>Cerrada el {declaration.closed_at}.</p>
      </div>
    );
  }
  if (["CANCELLED", "NOT_APPLICABLE"].includes(declaration.workflow_status)) {
    return <p style={{ color: "var(--muted)" }}>Esta declaración no admite cierre en su estado actual.</p>;
  }

  const filingMet = FILING_ELIGIBLE.has(declaration.filing_status);
  const taxMet = TAX_ELIGIBLE.has(declaration.tax_payment_status);
  const documentMet = DOCUMENT_ELIGIBLE.has(declaration.document_status);
  const allMet = filingMet && taxMet && documentMet;

  async function handleNormalClose() {
    const confirmed = window.confirm("¿Confirmas el cierre normal de esta declaración?");
    if (!confirmed) return;
    setSubmitting(true);
    setError(null);
    try {
      await onClose(false, null);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cerrar la declaración.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOverrideClose() {
    if (!reason.trim()) {
      setError("El cierre con pendientes requiere un motivo explícito.");
      return;
    }
    const confirmed = window.confirm(
      "Vas a cerrar esta declaración CON PENDIENTES. Esta es una acción excepcional y auditada. " +
        "¿Confirmas que cuentas con autorización para hacerlo?",
    );
    if (!confirmed) return;
    setSubmitting(true);
    setError(null);
    try {
      await onClose(true, reason);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo forzar el cierre.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
        <PreconditionRow label="Presentación" met={filingMet} />
        <PreconditionRow label="Documentos" met={documentMet} />
        <PreconditionRow label="Impuesto" met={taxMet} />
      </ul>

      {allMet && canClose && (
        <Button type="button" onClick={handleNormalClose} disabled={submitting}>
          {submitting ? "Cerrando…" : "Cerrar declaración"}
        </Button>
      )}
      {allMet && !canClose && (
        <p style={{ color: "var(--muted)" }}>No tienes permiso para cerrar declaraciones.</p>
      )}
      {!allMet && (
        <p style={{ color: "var(--muted)" }}>
          El cierre normal requiere que las tres precondiciones estén cumplidas.
        </p>
      )}

      {!allMet && canOverrideClose && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {!showOverride ? (
            <Button type="button" variant="danger" onClick={() => setShowOverride(true)}>
              Cerrar con pendientes (excepcional)
            </Button>
          ) : (
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
                Cierre excepcional con pendientes -- requiere motivo y queda auditado.
              </p>
              <Input
                label="Motivo del cierre con pendientes"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
              />
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <Button type="button" variant="danger" onClick={handleOverrideClose} disabled={submitting}>
                  {submitting ? "Cerrando…" : "Confirmar cierre con pendientes"}
                </Button>
                <Button type="button" variant="secondary" onClick={() => setShowOverride(false)}>
                  Cancelar
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
    </div>
  );
}
