"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ImportRunReview } from "@/components/taxSchedules/ImportRunReview";
import { apiClient } from "@/lib/apiClient";
import type { ImportRun } from "@/lib/types/taxSchedule";

export default function ImportRunPage() {
  const params = useParams<{ importRunId: string }>();
  const [importRun, setImportRun] = useState<ImportRun | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const data = await apiClient.get<ImportRun>(`/tax-schedules/import-runs/${params.importRunId}`);
    setImportRun(data);
    setLoading(false);
  }, [params.importRunId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  if (loading || !importRun) {
    return <p>Cargando…</p>;
  }

  return <ImportRunReview importRun={importRun} />;
}
