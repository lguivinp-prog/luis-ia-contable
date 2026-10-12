"use client";

// Revision HUMANA de la propuesta de la Ficha RUC: diff campo a campo, aceptacion selectiva y (solo en el alta asistida)
// correcciones de textos oficiales. Nada se aplica hasta que la persona confirma; los metadatos internos del negocio
// (telefono, correo, WhatsApp, honorario, etiquetas, notas, estado de servicio) nunca aparecen aqui porque nunca se tocan.
import { type ReactNode, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  type AcceptSelection,
  allChangedFields,
  buildSelection,
  canOverrideField,
  changedEntries,
  describeReviewStatus,
  formatDiffValue,
  isPendingReview,
} from "@/lib/fichaRucModel";
import type { FichaSnapshot } from "@/lib/types/fichaRuc";

interface Props {
  snapshot: FichaSnapshot;
  allowOverrides: boolean;
  busy: boolean;
  confirmLabel: string;
  onConfirm: (selection: AcceptSelection) => void;
  rejectLabel?: string;
  onReject?: () => void;
  extra?: ReactNode;
  readOnly?: boolean; // sin permiso de edicion: se ve la propuesta y su estado REAL, sin controles
}

export function FichaReviewPanel({ snapshot, allowOverrides, busy, confirmLabel, onConfirm, rejectLabel, onReject, extra, readOnly = false }: Props) {
  const pending = isPendingReview(snapshot) && !readOnly;
  const changed = changedEntries(snapshot.diff);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, string>>({});

  const toggle = (field: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });

  const selection = buildSelection(snapshot.diff, selected, edits);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <p style={{ color: "var(--muted)" }}>
        Origen: SUNAT (Ficha RUC) · obtenida el {new Date(snapshot.retrieved_at).toLocaleString("es-PE")} ·{" "}
        <strong>{describeReviewStatus(snapshot.review_status)}</strong>
      </p>
      {snapshot.malformed.length > 0 && (
        <p role="status" style={{ color: "var(--muted)" }}>
          Algunos datos de la Ficha no se pudieron interpretar y se omitieron ({snapshot.malformed.length}); no se inventó ningún valor.
        </p>
      )}

      {snapshot.diff.length === 0 ? (
        <p>La Ficha no trajo campos oficiales que comparar.</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" aria-label="Diferencias entre el cliente y la Ficha RUC">
            <thead>
              <tr>
                {["Campo oficial", "Actual en el sistema", "Propuesto por SUNAT", pending ? "Aceptar" : "Aceptado"].map((header) => (
                  <th key={header}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {snapshot.diff.map((entry) => {
                const accepted = pending ? selected.has(entry.field) : (snapshot.accepted_fields ?? []).includes(entry.field);
                return (
                  <tr key={entry.field} style={{ opacity: entry.changed ? 1 : 0.65 }}>
                    <td>{entry.label}</td>
                    <td>{formatDiffValue(entry.field, entry.current)}</td>
                    <td>
                      {formatDiffValue(entry.field, entry.proposed)}
                      {!entry.changed && <div style={{ color: "var(--muted)", fontSize: "0.78rem" }}>Sin cambios</div>}
                      {pending && allowOverrides && entry.changed && accepted && canOverrideField(entry.field) && (
                        <div className="field" style={{ marginTop: "0.35rem" }}>
                          <label htmlFor={`override-${entry.field}`}>Corregir antes de aceptar</label>
                          <input
                            id={`override-${entry.field}`}
                            className="input"
                            value={edits[entry.field] ?? String(entry.proposed ?? "")}
                            onChange={(e) => setEdits((current) => ({ ...current, [entry.field]: e.target.value }))}
                          />
                        </div>
                      )}
                    </td>
                    <td>
                      {pending ? (
                        entry.changed ? (
                          <input
                            type="checkbox"
                            aria-label={`Aceptar ${entry.label}`}
                            checked={accepted}
                            onChange={() => toggle(entry.field)}
                            disabled={busy}
                          />
                        ) : (
                          "—"
                        )
                      ) : accepted ? (
                        "Sí"
                      ) : (
                        "No"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {pending && (
        <>
          {extra}
          <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
            <Button type="button" variant="secondary" disabled={busy || changed.length === 0} onClick={() => setSelected(new Set(allChangedFields(snapshot.diff)))}>
              Seleccionar todos los cambios
            </Button>
            <Button type="button" disabled={busy || selection.accepted.length === 0} onClick={() => onConfirm(selection)}>
              {confirmLabel}
            </Button>
            {onReject && (
              <Button type="button" variant="danger" disabled={busy} onClick={onReject}>
                {rejectLabel ?? "Rechazar propuesta"}
              </Button>
            )}
          </div>
          <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
            Solo se actualizan los campos oficiales que marques. Los datos internos (contacto, honorarios, etiquetas, notas, estado de
            servicio) no se tocan.
          </p>
        </>
      )}
    </div>
  );
}
