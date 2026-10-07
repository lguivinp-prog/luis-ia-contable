"use client";

// Controles de los estados operativos MANUALES. Sin permiso `cockpit:update` solo muestran el valor (solo lectura).
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { StatusSemaphore, type SemaphoreEntry } from "@/components/ui/StatusSemaphore";
import { semaphoreOptions } from "@/lib/cockpitPresentation";
import { type ManualField, buildNotesUpdate, buildStatusUpdate, lastUpdateLabel } from "@/lib/cockpitWorkspace";
import type { OperationsState, OperationsUpdate } from "@/lib/types/cockpit";

interface Props {
  operations: OperationsState;
  canUpdate: boolean;
  fields: { field: ManualField; label: string; map: Record<string, SemaphoreEntry> }[];
  showNotes?: boolean;
  busy: boolean;
  onSubmit: (update: OperationsUpdate) => Promise<void>;
}

export function OperationsControls({ operations, canUpdate, fields, showNotes = false, busy, onSubmit }: Props) {
  const [notesDraft, setNotesDraft] = useState<string | null>(null);
  const notes = notesDraft ?? operations.notes ?? "";
  const updated = lastUpdateLabel(operations);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
        {fields.map(({ field, label, map }) =>
          canUpdate ? (
            <Select
              key={field}
              id={`ops-${field}`}
              label={label}
              options={semaphoreOptions(map)}
              value={operations[field]}
              disabled={busy}
              onChange={(e) => void onSubmit(buildStatusUpdate(operations, field, e.target.value))}
            />
          ) : (
            <div key={field} className="field">
              <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--muted)" }}>{label}</span>
              <StatusSemaphore status={operations[field]} map={map} />
            </div>
          ),
        )}
      </div>

      {showNotes && (
        <div className="field">
          <label htmlFor="ops-notes">Notas del periodo</label>
          {canUpdate ? (
            <>
              <textarea
                id="ops-notes"
                className="input"
                rows={3}
                maxLength={2000}
                value={notes}
                disabled={busy}
                onChange={(e) => setNotesDraft(e.target.value)}
              />
              <div>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy || notesDraft === null || notes.trim() === (operations.notes ?? "")}
                  onClick={async () => {
                    await onSubmit(buildNotesUpdate(operations, notes));
                    setNotesDraft(null);
                  }}
                >
                  Guardar notas
                </Button>
              </div>
            </>
          ) : (
            <p style={{ whiteSpace: "pre-wrap", color: operations.notes ? undefined : "var(--muted)" }}>
              {operations.notes ?? "Sin notas."}
            </p>
          )}
        </div>
      )}

      {!canUpdate && (
        <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          Solo lectura: tu rol no puede cambiar estos estados.
        </p>
      )}
      {updated && <p style={{ color: "var(--muted)", fontSize: "0.82rem" }}>{updated}</p>}
    </div>
  );
}
