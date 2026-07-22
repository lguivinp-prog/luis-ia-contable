"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import type { ObligationCatalogItem } from "@/lib/types/obligation";

interface ObligationCatalogPickerProps {
  availableObligations: ObligationCatalogItem[];
  onAssign: (obligationId: string) => Promise<void>;
}

export function ObligationCatalogPicker({ availableObligations, onAssign }: ObligationCatalogPickerProps) {
  const [selectedId, setSelectedId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleAssign() {
    if (!selectedId) return;
    setSubmitting(true);
    try {
      await onAssign(selectedId);
      setSelectedId("");
    } finally {
      setSubmitting(false);
    }
  }

  if (availableObligations.length === 0) {
    return <p style={{ color: "var(--muted)" }}>Todas las obligaciones del catálogo ya están asignadas.</p>;
  }

  return (
    <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end" }}>
      <Select
        label="Obligación del catálogo"
        name="obligation"
        placeholder="Selecciona una obligación"
        options={availableObligations.map((item) => ({ value: item.id, label: `${item.code} — ${item.name}` }))}
        value={selectedId}
        onChange={(event) => setSelectedId(event.target.value)}
      />
      <Button type="button" disabled={!selectedId || submitting} onClick={handleAssign}>
        {submitting ? "Asignando…" : "Asignar"}
      </Button>
    </div>
  );
}
