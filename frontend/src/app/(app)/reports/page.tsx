"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { apiClient, ApiError } from "@/lib/apiClient";

const REPORT_TYPE_OPTIONS = [
  { value: "DECLARATIONS", label: "Declaraciones" },
  { value: "DECLARATION_DOCUMENTS", label: "Documentos de declaraciones" },
  { value: "TAX_ASSESSMENTS", label: "Determinaciones tributarias" },
  { value: "TAX_PAYMENTS", label: "Pagos tributarios" },
  { value: "FEE_AGREEMENTS", label: "Acuerdos de honorarios" },
  { value: "FEE_CHARGES", label: "Cargos de honorarios" },
  { value: "FEE_PAYMENTS", label: "Pagos de honorarios" },
  { value: "FEE_RECEIPTS", label: "Recibos de honorarios" },
  { value: "CLIENTS", label: "Contribuyentes" },
  { value: "AUDIT_LOG", label: "Registro de auditoría" },
  { value: "TASKS", label: "Tareas operativas" },
];

const FORMAT_OPTIONS = [
  { value: "csv", label: "CSV" },
  { value: "xlsx", label: "Excel (.xlsx)" },
];

export default function ReportsPage() {
  const [reportType, setReportType] = useState(REPORT_TYPE_OPTIONS[0].value);
  const [format, setFormat] = useState<"csv" | "xlsx">("csv");
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleExport() {
    setDownloading(true);
    setError(null);
    try {
      const { blob, filename } = await apiClient.postForFile("/reports/export", {
        report_type: reportType,
        format,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo generar el reporte.");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "480px" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Reportes</h1>
      <p style={{ color: "var(--muted)" }}>
        Exporta datos ficticios del tenant en CSV o Excel. Cada exportación queda registrada en el
        historial de auditoría.
      </p>

      <Select
        label="Tipo de reporte"
        options={REPORT_TYPE_OPTIONS}
        value={reportType}
        onChange={(e) => setReportType(e.target.value)}
      />
      <Select
        label="Formato"
        options={FORMAT_OPTIONS}
        value={format}
        onChange={(e) => setFormat(e.target.value as "csv" | "xlsx")}
      />

      <Button type="button" onClick={handleExport} disabled={downloading}>
        {downloading ? "Generando…" : "Descargar"}
      </Button>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
    </div>
  );
}
