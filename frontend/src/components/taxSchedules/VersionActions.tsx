"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { apiClient } from "@/lib/apiClient";
import type { TaxScheduleVersion, TaxScheduleVersionStatus } from "@/lib/types/taxSchedule";

interface VersionActionsProps {
  version: TaxScheduleVersion;
  onChanged: () => Promise<void>;
}

const MUTABLE: TaxScheduleVersionStatus[] = ["DRAFT", "IMPORTED"];

export function VersionActions({ version, onChanged }: VersionActionsProps) {
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<void>) {
    setSubmitting(true);
    setError(null);
    try {
      await action();
      setComment("");
      setReason("");
      await onChanged();
    } catch {
      setError("No se pudo completar la acción. Verifica el estado de la versión y los campos requeridos.");
    } finally {
      setSubmitting(false);
    }
  }

  if (version.status === "APPROVED" || version.status === "SUPERSEDED") {
    return (
      <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
        Esta versión está {version.status === "APPROVED" ? "aprobada" : "reemplazada"} y ya no admite
        cambios directos: cualquier corrección requiere importar y aprobar una versión nueva con el mismo
        alcance (fuente, año fiscal, tipo de cronograma y categoría), lo que la reemplazará automáticamente.
      </p>
    );
  }

  if (version.status === "REJECTED" || version.status === "CANCELLED") {
    return <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>Esta versión no admite más acciones.</p>;
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <h2 style={{ fontSize: "1.05rem" }}>Acciones sobre la versión</h2>

      {MUTABLE.includes(version.status) && (
        <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
          <Input label="Comentario (opcional)" name="submit-comment" value={comment} onChange={(e) => setComment(e.target.value)} />
          <Button
            type="button"
            disabled={submitting}
            onClick={() =>
              run(() =>
                apiClient.post(`/tax-schedules/versions/${version.id}/submit-for-review`, {
                  comment: comment.trim() || null,
                })
              )
            }
          >
            Enviar a revisión
          </Button>
        </div>
      )}

      {version.status === "PENDING_REVIEW" && (
        <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
          <Input
            label="Comentario de aprobación (obligatorio)"
            name="approve-comment"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <Button
            type="button"
            disabled={!comment.trim() || submitting}
            onClick={() =>
              run(() => apiClient.post(`/tax-schedules/versions/${version.id}/approve`, { comment: comment.trim() }))
            }
          >
            Aprobar (confirma explícitamente esta versión)
          </Button>
        </div>
      )}

      {(version.status === "PENDING_REVIEW" || MUTABLE.includes(version.status)) && (
        <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
          <Input label="Motivo (obligatorio)" name="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          {version.status === "PENDING_REVIEW" && (
            <Button
              type="button"
              variant="danger"
              disabled={!reason.trim() || submitting}
              onClick={() =>
                run(() => apiClient.post(`/tax-schedules/versions/${version.id}/reject`, { reason: reason.trim() }))
              }
            >
              Rechazar
            </Button>
          )}
          <Button
            type="button"
            variant="secondary"
            disabled={!reason.trim() || submitting}
            onClick={() =>
              run(() => apiClient.post(`/tax-schedules/versions/${version.id}/cancel`, { reason: reason.trim() }))
            }
          >
            Cancelar versión
          </Button>
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}
    </section>
  );
}
