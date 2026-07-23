"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ApiError } from "@/lib/apiClient";
import type { TaxAssessment } from "@/lib/types/taxAssessment";

interface TaxAssessmentPanelProps {
  assessments: TaxAssessment[];
  canAct: boolean;
  onCreate: (grossTaxAmount: string, creditsApplied: string, observations: string) => Promise<void>;
  onCorrect: (
    grossTaxAmount: string,
    creditsApplied: string,
    observations: string,
    reason: string,
  ) => Promise<void>;
}

export function TaxAssessmentPanel({ assessments, canAct, onCreate, onCorrect }: TaxAssessmentPanelProps) {
  const current = assessments.find((a) => a.is_current) ?? null;
  const [grossTaxAmount, setGrossTaxAmount] = useState("");
  const [creditsApplied, setCreditsApplied] = useState("0");
  const [observations, setObservations] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!grossTaxAmount) return;
    if (current && (!reason || !reason.trim())) {
      setError("La corrección requiere un motivo explícito.");
      return;
    }
    if (current) {
      const confirmed = window.confirm(
        "Vas a corregir la determinación vigente. Esta acción queda registrada en el historial. ¿Confirmas?",
      );
      if (!confirmed) return;
    }
    setSubmitting(true);
    setError(null);
    try {
      if (current) {
        await onCorrect(grossTaxAmount, creditsApplied, observations, reason);
      } else {
        await onCreate(grossTaxAmount, creditsApplied, observations);
      }
      setGrossTaxAmount("");
      setCreditsApplied("0");
      setObservations("");
      setReason("");
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo registrar la determinación.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {current ? (
        <dl>
          <dt>Secuencia</dt>
          <dd>
            #{current.sequence_number} ({current.assessment_type === "ORIGINAL" ? "original" : "corrección"})
          </dd>
          <dt>Impuesto bruto</dt>
          <dd>{current.gross_tax_amount}</dd>
          <dt>Créditos aplicados</dt>
          <dd>{current.credits_applied}</dd>
          <dt>Total por pagar</dt>
          <dd>{current.total_payable}</dd>
          <dt>Saldo a favor</dt>
          <dd>{current.remaining_credit}</dd>
          <dt>Determinado el</dt>
          <dd>{current.assessed_at}</dd>
        </dl>
      ) : (
        <p style={{ color: "var(--muted)" }}>Esta declaración todavía no tiene una determinación tributaria.</p>
      )}

      {canAct && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", maxWidth: "360px" }}>
          <h3 style={{ fontSize: "1rem" }}>{current ? "Registrar corrección" : "Registrar determinación inicial"}</h3>
          <Input
            label="Impuesto bruto"
            type="number"
            step="0.01"
            min="0"
            value={grossTaxAmount}
            onChange={(e) => setGrossTaxAmount(e.target.value)}
          />
          <Input
            label="Créditos aplicados"
            type="number"
            step="0.01"
            min="0"
            value={creditsApplied}
            onChange={(e) => setCreditsApplied(e.target.value)}
          />
          <Input
            label="Observaciones"
            value={observations}
            onChange={(e) => setObservations(e.target.value)}
          />
          {current && (
            <Input
              label="Motivo de la corrección"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
            />
          )}
          <Button type="button" onClick={handleSubmit} disabled={submitting || !grossTaxAmount}>
            {submitting ? "Guardando…" : current ? "Registrar corrección" : "Registrar determinación"}
          </Button>
          {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
        </div>
      )}

      {assessments.length > 0 && (
        <section>
          <h3 style={{ fontSize: "1rem" }}>Historial de determinaciones</h3>
          <ul>
            {[...assessments]
              .sort((a, b) => a.sequence_number - b.sequence_number)
              .map((a) => (
                <li key={a.id}>
                  #{a.sequence_number} [{a.assessment_type}] bruto {a.gross_tax_amount}, créditos{" "}
                  {a.credits_applied} → total {a.total_payable} ({a.assessed_at})
                  {a.is_current ? " — vigente" : ""}
                </li>
              ))}
          </ul>
        </section>
      )}
    </div>
  );
}
