"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { apiClient } from "@/lib/apiClient";
import type { CompareVersionsResult, TaxScheduleVersion } from "@/lib/types/taxSchedule";

interface CompareVersionsPanelProps {
  currentVersion: TaxScheduleVersion;
  siblingVersions: TaxScheduleVersion[];
}

export function CompareVersionsPanel({ currentVersion, siblingVersions }: CompareVersionsPanelProps) {
  const [otherVersionId, setOtherVersionId] = useState("");
  const [result, setResult] = useState<CompareVersionsResult | null>(null);
  const [loading, setLoading] = useState(false);

  const options = siblingVersions
    .filter((v) => v.id !== currentVersion.id)
    .map((v) => ({ value: v.id, label: v.version_label }));

  async function handleCompare() {
    if (!otherVersionId) return;
    setLoading(true);
    try {
      const data = await apiClient.get<CompareVersionsResult>(
        `/tax-schedules/versions/compare?version_a=${currentVersion.id}&version_b=${otherVersionId}`
      );
      setResult(data);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <h2 style={{ fontSize: "1.05rem" }}>Comparar con otra versión</h2>
      <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end" }}>
        <Select
          label="Comparar con"
          name="other_version"
          placeholder="Selecciona una versión"
          options={options}
          value={otherVersionId}
          onChange={(e) => setOtherVersionId(e.target.value)}
        />
        <Button type="button" disabled={!otherVersionId || loading} onClick={handleCompare}>
          {loading ? "Comparando…" : "Comparar"}
        </Button>
      </div>

      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", fontSize: "0.9rem" }}>
          <p>
            {result.version_a} vs. {result.version_b}
          </p>
          <p>Agregadas en {result.version_b}: {result.added.length}</p>
          <p>Eliminadas respecto a {result.version_a}: {result.removed.length}</p>
          <p>Con fecha de vencimiento distinta: {result.modified.length}</p>
          {result.modified.map((m, i) => (
            <p key={`${m.tax_period}-${i}`} style={{ color: "var(--muted)" }}>
              {m.tax_period}: {m.from_due_date_effective} → {m.to_due_date_effective}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
