"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { apiClient } from "@/lib/apiClient";
import type { ImportRun } from "@/lib/types/taxSchedule";

const CURRENT_YEAR = new Date().getFullYear();

export function ImportCsvForm({ sourceId }: { sourceId: string }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [fiscalYear, setFiscalYear] = useState(String(CURRENT_YEAR));
  const [scheduleType, setScheduleType] = useState("GENERAL");
  const [scopeCategoryCode, setScopeCategoryCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!file || !fiscalYear) return;
    setSubmitting(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("fiscal_year", fiscalYear);
      formData.append("schedule_type", scheduleType);
      if (scopeCategoryCode.trim()) formData.append("scope_category_code", scopeCategoryCode.trim());

      const importRun = await apiClient.uploadFile<ImportRun>(
        `/tax-schedules/sources/${sourceId}/import-runs`,
        formData
      );
      router.push(`/tax-schedules/import-runs/${importRun.id}`);
    } catch {
      setError("No se pudo procesar el archivo. Verifica que sea un CSV UTF-8 válido con las columnas requeridas.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <h2 style={{ fontSize: "1.1rem" }}>Importar cronograma (CSV)</h2>
      <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
        Columnas requeridas: tax_period, applicable_last_digits, required_category_code, priority,
        due_date_original, due_date_effective, due_date_type, notes.
      </p>

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
        <div className="field">
          <label htmlFor="csv-file">Archivo CSV</label>
          <input
            id="csv-file"
            className="input"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <Input
          label="Año fiscal"
          name="fiscal_year"
          type="number"
          value={fiscalYear}
          onChange={(e) => setFiscalYear(e.target.value)}
        />
        <Input
          label="Tipo de cronograma"
          name="schedule_type"
          value={scheduleType}
          onChange={(e) => setScheduleType(e.target.value.toUpperCase())}
        />
        <Input
          label="Categoría de alcance (opcional)"
          name="scope_category_code"
          value={scopeCategoryCode}
          onChange={(e) => setScopeCategoryCode(e.target.value.toUpperCase())}
        />
        <Button type="button" disabled={!file || !fiscalYear || submitting} onClick={handleSubmit}>
          {submitting ? "Subiendo…" : "Subir y validar"}
        </Button>
      </div>
      {error && <div className="error-banner">{error}</div>}
    </section>
  );
}
