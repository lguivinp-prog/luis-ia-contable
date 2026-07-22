"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApprovalHistoryList } from "@/components/taxSchedules/ApprovalHistoryList";
import { CompareVersionsPanel } from "@/components/taxSchedules/CompareVersionsPanel";
import { EntriesTable } from "@/components/taxSchedules/EntriesTable";
import { VersionActions } from "@/components/taxSchedules/VersionActions";
import { apiClient } from "@/lib/apiClient";
import type {
  ApprovalHistoryEntry,
  TaxScheduleEntry,
  TaxScheduleVersion,
} from "@/lib/types/taxSchedule";

const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Borrador",
  IMPORTED: "Importada",
  PENDING_REVIEW: "En revisión",
  APPROVED: "Aprobada",
  SUPERSEDED: "Reemplazada",
  REJECTED: "Rechazada",
  CANCELLED: "Cancelada",
};

export default function TaxScheduleVersionPage() {
  const params = useParams<{ versionId: string }>();
  const versionId = params.versionId;

  const [version, setVersion] = useState<TaxScheduleVersion | null>(null);
  const [entries, setEntries] = useState<TaxScheduleEntry[]>([]);
  const [history, setHistory] = useState<ApprovalHistoryEntry[]>([]);
  const [siblingVersions, setSiblingVersions] = useState<TaxScheduleVersion[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const versionData = await apiClient.get<TaxScheduleVersion>(`/tax-schedules/versions/${versionId}`);
    const [entriesData, historyData, siblingsData] = await Promise.all([
      apiClient.get<TaxScheduleEntry[]>(`/tax-schedules/versions/${versionId}/entries`),
      apiClient.get<ApprovalHistoryEntry[]>(`/tax-schedules/versions/${versionId}/approval-history`),
      apiClient.get<TaxScheduleVersion[]>(`/tax-schedules/versions?source_id=${versionData.source_id}`),
    ]);
    setVersion(versionData);
    setEntries(entriesData);
    setHistory(historyData);
    setSiblingVersions(siblingsData);
    setLoading(false);
  }, [versionId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  if (loading || !version) {
    return <p>Cargando…</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div>
        <Link href={`/tax-schedules/${version.source_id}`} style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          ← Volver a la fuente
        </Link>
        <h1 style={{ fontSize: "1.4rem", marginTop: "0.4rem" }}>Versión {version.version_label}</h1>
        <p style={{ color: "var(--muted)" }}>
          {STATUS_LABEL[version.status] ?? version.status} · Año fiscal {version.fiscal_year} ·{" "}
          {version.scope_category_code ?? "Alcance general"}
        </p>
        {version.normative_reference && <p style={{ color: "var(--muted)" }}>Referencia: {version.normative_reference}</p>}
      </div>

      <VersionActions version={version} onChanged={reload} />
      <EntriesTable entries={entries} />
      <ApprovalHistoryList history={history} />
      <CompareVersionsPanel currentVersion={version} siblingVersions={siblingVersions} />

      <a
        href={`/api/v1/tax-schedules/versions/${version.id}/entries.csv`}
        style={{ color: "var(--muted)", fontSize: "0.85rem" }}
      >
        Descargar entradas (CSV)
      </a>
    </div>
  );
}
