"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ImportCsvForm } from "@/components/taxSchedules/ImportCsvForm";
import { VersionsTable } from "@/components/taxSchedules/VersionsTable";
import { apiClient } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import type { TaxScheduleSource, TaxScheduleVersion } from "@/lib/types/taxSchedule";

export default function TaxScheduleSourcePage() {
  const params = useParams<{ sourceId: string }>();
  const sourceId = params.sourceId;
  const { user } = useCurrentUser();
  const canManage = hasPermission(user, "tax_schedule:manage");

  const [source, setSource] = useState<TaxScheduleSource | null>(null);
  const [versions, setVersions] = useState<TaxScheduleVersion[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const [sourceData, versionsData] = await Promise.all([
      apiClient.get<TaxScheduleSource>(`/tax-schedules/sources/${sourceId}`),
      apiClient.get<TaxScheduleVersion[]>(`/tax-schedules/versions?source_id=${sourceId}`),
    ]);
    setSource(sourceData);
    setVersions(versionsData);
    setLoading(false);
  }, [sourceId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  if (loading || !source) {
    return <p>Cargando…</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div>
        <Link href="/tax-schedules" style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          ← Volver a fuentes
        </Link>
        <h1 style={{ fontSize: "1.4rem", marginTop: "0.4rem" }}>{source.name}</h1>
        {source.description && <p style={{ color: "var(--muted)" }}>{source.description}</p>}
      </div>

      <VersionsTable versions={versions} />
      {canManage && <ImportCsvForm sourceId={sourceId} />}
    </div>
  );
}
