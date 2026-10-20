"use client";

// Reporte de control SIRE (cliente, periodo y componente): compacto, imprimible y verificable (huella SHA-256 del contenido). NO es una constancia oficial de presentacion.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { fetchReport } from "@/lib/sireControl";
import { CONCEPT_LABEL, DECISION_LABEL, OFFICIAL_FILING_NOTICE, describeSireError, formatConceptValue, formatDifference, formatBytes, summaryInfo } from "@/lib/sireControlModel";
import type { SireDecision, SireReport } from "@/lib/types/sireControl";

function when(value: unknown): string {
  if (typeof value !== "string") return "—";
  const d = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString("es-PE", { dateStyle: "medium", timeStyle: value.length === 10 ? undefined : "short" });
}

export default function SireControlReportPage() {
  const params = useParams<{ id: string; controlId: string }>();
  const [report, setReport] = useState<SireReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void Promise.resolve()
      .then(() => fetchReport(params.controlId))
      .then((r) => alive && setReport(r))
      .catch((err) => alive && setError(describeSireError((err as { detail?: unknown } | undefined)?.detail).message));
    return () => {
      alive = false;
    };
  }, [params.controlId]);

  if (error) return <p role="alert">{error}</p>;
  if (!report) return <p role="status">Generando reporte…</p>;

  const id = report.identification;
  const currency = String((report.sources.SUNAT ?? report.sources.DARWIN)?.currency ?? "PEN");
  const review = report.review as Record<string, string | number | boolean | null> | null;
  return (
    <article data-testid="sire-report" style={{ display: "flex", flexDirection: "column", gap: "1rem", maxWidth: "900px" }}>
      <div className="no-print" style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <Link className="btn btn-secondary" href={`/cockpit/${params.id}?period=${id.tax_period_id}&tab=sire`}>← Volver al workspace</Link>
        <Button type="button" onClick={() => window.print()}>Imprimir / guardar como PDF</Button>
      </div>
      <header>
        <h1 style={{ fontSize: "1.3rem", margin: 0 }}>Reporte de control {String(id.component)} — {String(id.tax_period_code)}</h1>
        <p style={{ margin: "0.25rem 0 0" }}>{String(id.client_business_name)} · RUC {String(id.client_ruc)}</p>
        <p style={{ color: "var(--muted)", fontSize: "0.85rem", margin: "0.25rem 0 0" }}>
          Estado del control: <strong>{summaryInfo(String(id.summary_code)).label}</strong> · generado el {when(report.generated_at)} · huella del reporte <code data-testid="report-hash">{report.report_hash.slice(0, 16)}…</code>
        </p>
      </header>
      <p role="note" data-testid="report-disclaimer" style={{ border: "1px solid var(--warning, #b26a00)", borderRadius: "8px", padding: "0.6rem", margin: 0, fontSize: "0.9rem" }}>
        <strong>Advertencia:</strong> {report.disclaimer}
      </p>

      <section aria-label="Fuentes">
        <h2 style={{ fontSize: "1.05rem" }}>Fuentes de los datos</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: "0.7rem" }}>
          {(["SUNAT", "DARWIN"] as const).map((origin) => {
            const s = report.sources[origin];
            return (
              <div key={origin} style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.7rem", fontSize: "0.9rem" }}>
                <strong>{origin === "SUNAT" ? "SUNAT (oficial)" : "DARWIN / CONTASIS"}</strong>
                {s ? (
                  <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>
                    <li>Versión {String(s.version)} · {s.entry_mode === "IMPORT" ? "importada de archivo" : "ingreso manual"}</li>
                    <li>{String(s.source_label)} · fuente del {when(s.source_date)}</li>
                    <li>Incorporada el {when(s.created_at)}</li>
                  </ul>
                ) : (
                  <p style={{ margin: "0.3rem 0 0", color: "var(--muted)" }}>Sin información incorporada.</p>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section aria-label="Comparación">
        <h2 style={{ fontSize: "1.05rem" }}>Cantidades e importes comparados</h2>
        {report.comparison.concepts.length === 0 ? (
          <p>Comparación pendiente o no comparable ({report.comparison.status}).</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.9rem" }}>
              <thead><tr>{["Concepto", "SUNAT", "DARWIN / CONTASIS", "Diferencia (DARWIN − SUNAT)"].map((h) => <th key={h} scope="col" style={{ textAlign: "left", padding: "0.3rem 0.5rem", borderBottom: "1px solid var(--border, #ddd)" }}>{h}</th>)}</tr></thead>
              <tbody>
                {report.comparison.concepts.map((c) => (
                  <tr key={c.concept}>
                    <th scope="row" style={{ textAlign: "left", padding: "0.3rem 0.5rem", fontWeight: 500 }}>{CONCEPT_LABEL[c.concept]}</th>
                    <td style={{ padding: "0.3rem 0.5rem" }}>{formatConceptValue(c.concept, c.sunat, currency)}</td>
                    <td style={{ padding: "0.3rem 0.5rem" }}>{formatConceptValue(c.concept, c.darwin, currency)}</td>
                    <td style={{ padding: "0.3rem 0.5rem", fontWeight: 600 }}>{formatDifference(c.difference, c.concept)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p style={{ fontSize: "0.85rem" }}>
          {report.differences.length === 0 ? "No se detectaron diferencias en los conceptos comparables." : `Diferencias detectadas: ${report.differences.length}.`}
          {report.comparison.tolerance ? ` Tolerancia explícita aplicada: ${report.comparison.tolerance} (${report.comparison.tolerance_reason ?? "sin motivo"}).` : " Comparación exacta (sin tolerancia)."}
        </p>
      </section>

      <section aria-label="Archivos de respaldo">
        <h2 style={{ fontSize: "1.05rem" }}>Archivos de respaldo</h2>
        {report.files.length === 0 ? <p>Sin archivos de respaldo.</p> : (
          <ul style={{ paddingLeft: "1.1rem", fontSize: "0.9rem" }}>
            {report.files.map((f) => (
              <li key={f.id}>{f.original_filename} · {f.origin} · {formatBytes(f.size_bytes)} · {when(f.uploaded_at)}<br /><code style={{ fontSize: "0.75rem" }}>SHA-256 {f.sha256}</code><br /><span style={{ color: "var(--muted)", fontSize: "0.75rem" }}>ID {f.id}</span></li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Revisión profesional">
        <h2 style={{ fontSize: "1.05rem" }}>Revisión profesional</h2>
        {review ? (
          <p data-testid="report-review">
            {DECISION_LABEL[review.decision as SireDecision]} · versión {String(review.version)} · responsable {String(review.reviewed_by_name ?? review.reviewed_by)} · {when(review.reviewed_at)}
            {review.stale ? " · DESACTUALIZADA: una fuente cambió después" : ""}
            {review.notes ? <><br />Observación: {String(review.notes)}</> : null}
          </p>
        ) : <p>Sin revisión profesional registrada.</p>}
        {report.review_history.length > 1 && <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>Revisiones anteriores: {report.review_history.length - 1} (consultables en el historial).</p>}
      </section>
      <p style={{ color: "var(--muted)", fontSize: "0.8rem" }}>{OFFICIAL_FILING_NOTICE}</p>
    </article>
  );
}
