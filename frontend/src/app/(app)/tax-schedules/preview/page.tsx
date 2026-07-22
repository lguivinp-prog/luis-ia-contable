"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { DemoDataBanner } from "@/components/taxSchedules/DemoDataBanner";
import { DueDatePreviewForm } from "@/components/taxSchedules/DueDatePreviewForm";
import { apiClient } from "@/lib/apiClient";
import type { TaxScheduleSource } from "@/lib/types/taxSchedule";

export default function TaxScheduleDueDatePreviewPage() {
  const [sources, setSources] = useState<TaxScheduleSource[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const data = await apiClient.get<TaxScheduleSource[]>("/tax-schedules/sources");
    setSources(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div>
        <Link href="/tax-schedules" style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          ← Volver a cronogramas
        </Link>
        <h1 style={{ fontSize: "1.4rem", marginTop: "0.4rem" }}>Vista previa de vencimiento</h1>
      </div>

      <DemoDataBanner />

      {loading ? <p>Cargando…</p> : <DueDatePreviewForm sources={sources} />}
    </div>
  );
}
