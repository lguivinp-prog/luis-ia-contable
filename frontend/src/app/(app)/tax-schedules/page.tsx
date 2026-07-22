"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { DemoDataBanner } from "@/components/taxSchedules/DemoDataBanner";
import { SourcesPanel } from "@/components/taxSchedules/SourcesPanel";
import { apiClient } from "@/lib/apiClient";
import type { ObligationCatalogItem } from "@/lib/types/obligation";
import type { TaxScheduleSource } from "@/lib/types/taxSchedule";

export default function TaxSchedulesPage() {
  const [sources, setSources] = useState<TaxScheduleSource[]>([]);
  const [obligations, setObligations] = useState<ObligationCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const [sourcesData, obligationsData] = await Promise.all([
      apiClient.get<TaxScheduleSource[]>("/tax-schedules/sources"),
      apiClient.get<ObligationCatalogItem[]>("/obligations/catalog"),
    ]);
    setSources(sourcesData);
    setObligations(obligationsData);
    setLoading(false);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleCreate(
    obligationId: string,
    name: string,
    description: string | null,
    referenceUrl: string | null
  ) {
    await apiClient.post("/tax-schedules/sources", {
      obligation_id: obligationId,
      name,
      description,
      reference_url: referenceUrl,
    });
    await reload();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: "1.4rem" }}>Cronogramas tributarios</h1>
        <Link href="/tax-schedules/preview">
          <Button type="button" variant="secondary">
            Vista previa de vencimiento
          </Button>
        </Link>
      </div>

      <DemoDataBanner />

      {loading ? <p>Cargando…</p> : <SourcesPanel sources={sources} obligations={obligations} onCreate={handleCreate} />}
    </div>
  );
}
