"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Table } from "@/components/ui/Table";
import { apiClient } from "@/lib/apiClient";
import type { ImportRun, TaxScheduleVersion } from "@/lib/types/taxSchedule";

const STATUS_LABEL: Record<string, string> = {
  VALIDATED: "Validado, pendiente de confirmar",
  REJECTED: "Rechazado",
  CONFIRMED: "Confirmado",
};

export function ImportRunReview({ importRun }: { importRun: ImportRun }) {
  const router = useRouter();
  const [versionLabel, setVersionLabel] = useState("");
  const [normativeReference, setNormativeReference] = useState("");
  const [sourceDocumentUrl, setSourceDocumentUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasErrors = !!importRun.validation_errors && importRun.validation_errors.length > 0;
  const canConfirm = importRun.status === "VALIDATED";

  async function handleConfirm() {
    if (!versionLabel.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const version = await apiClient.post<TaxScheduleVersion>(
        `/tax-schedules/import-runs/${importRun.id}/confirm`,
        {
          version_label: versionLabel.trim(),
          normative_reference: normativeReference.trim() || null,
          source_document_url: sourceDocumentUrl.trim() || null,
        }
      );
      router.push(`/tax-schedules/versions/${version.id}`);
    } catch {
      setError("No se pudo confirmar la importación.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReject() {
    setSubmitting(true);
    setError(null);
    try {
      await apiClient.post(`/tax-schedules/import-runs/${importRun.id}/reject`);
      router.push(`/tax-schedules/${importRun.source_id}`);
    } catch {
      setError("No se pudo rechazar la importación.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div>
        <h1 style={{ fontSize: "1.4rem" }}>Vista previa de importación: {importRun.original_filename}</h1>
        <p style={{ color: "var(--muted)" }}>
          Estado: {STATUS_LABEL[importRun.status] ?? importRun.status} · {importRun.row_count ?? 0} filas ·
          checksum {importRun.file_checksum.slice(0, 12)}…
        </p>
      </div>

      {hasErrors && (
        <section style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <h2 style={{ fontSize: "1.05rem" }}>Errores de validación</h2>
          <Table
            rows={importRun.validation_errors ?? []}
            rowKey={(e) => `${e.row}:${e.message}`}
            columns={[
              { key: "row", header: "Fila", render: (e) => e.row },
              { key: "message", header: "Mensaje", render: (e) => e.message },
            ]}
          />
        </section>
      )}

      {importRun.diff_summary && (
        <section style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <h2 style={{ fontSize: "1.05rem" }}>Diferencias respecto a la versión aprobada previa</h2>
          {!importRun.diff_summary.has_previous ? (
            <p style={{ color: "var(--muted)" }}>No hay una versión aprobada previa con el mismo alcance.</p>
          ) : (
            <>
              <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
                Comparado con: {importRun.diff_summary.previous_version_label}
              </p>
              <p>Agregadas: {importRun.diff_summary.added.length}</p>
              <p>Eliminadas: {importRun.diff_summary.removed.length}</p>
              <p>Con fecha modificada: {importRun.diff_summary.modified_dates.length}</p>
            </>
          )}
        </section>
      )}

      {!hasErrors && canConfirm && (
        <section style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <h2 style={{ fontSize: "1.05rem" }}>Confirmar importación</h2>
          <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
            Al confirmar se crea una versión nueva en estado IMPORTADA (aún requiere revisión y aprobación
            explícitas antes de poder usarse en la vista previa de vencimientos).
          </p>
          <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
            <Input
              label="Etiqueta de versión"
              name="version_label"
              value={versionLabel}
              onChange={(e) => setVersionLabel(e.target.value)}
            />
            <Input
              label="Referencia normativa"
              name="normative_reference"
              value={normativeReference}
              onChange={(e) => setNormativeReference(e.target.value)}
            />
            <Input
              label="URL del documento fuente"
              name="source_document_url"
              value={sourceDocumentUrl}
              onChange={(e) => setSourceDocumentUrl(e.target.value)}
            />
            <Button type="button" disabled={!versionLabel.trim() || submitting} onClick={handleConfirm}>
              {submitting ? "Confirmando…" : "Confirmar e importar"}
            </Button>
            <Button type="button" variant="danger" disabled={submitting} onClick={handleReject}>
              Rechazar importación
            </Button>
          </div>
        </section>
      )}
      {error && <div className="error-banner">{error}</div>}
    </div>
  );
}
