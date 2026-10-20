"use client";

// Seccion de UN componente del expediente SIRE (RVIE o RCE) de un cliente y periodo: fuentes (SUNAT vs DARWIN/CONTASIS), comparacion, diferencias, evidencias, revision
// profesional, historial y reporte. NUNCA presenta, paga ni modifica estados tributarios: solo organiza y documenta lo que aporta el contador.
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { SireImportWizard } from "@/components/sire/SireImportWizard";
import { SireSourceForm } from "@/components/sire/SireSourceForm";
import { attachEvidence, downloadUrl, fetchHistory, recordReview, setTolerance, verifyFile } from "@/lib/sireControl";
import {
  COMPONENT_LABEL,
  CONCEPT_LABEL,
  DECISION_LABEL,
  type DescribedError,
  ORIGIN_LABEL,
  allowedDecisions,
  decisionNeedsNotes,
  describeSireError,
  formatBytes,
  formatConceptValue,
  formatDifference,
  formatMoney,
  summaryInfo,
} from "@/lib/sireControlModel";
import type { SireControl, SireDecision, SireFile, SireFileIntegrity, SireHistory, SireOrigin, SireSource } from "@/lib/types/sireControl";

const TONE: Record<string, string> = {
  ok: "var(--success, #2e7d32)",
  warn: "var(--warning, #b26a00)",
  danger: "var(--danger, #c0392b)",
  info: "var(--info, #1565c0)",
  muted: "var(--muted)",
};

type Mode = { kind: "manual" | "import"; origin: SireOrigin } | null;

interface Props {
  control: SireControl;
  clientId: string;
  periodId: string;
  canWrite: boolean;
  onChanged: () => void;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString("es-PE", { dateStyle: "medium", timeStyle: value.length === 10 ? undefined : "short" });
}

export function SireComponentSection({ control, clientId, periodId, canWrite, onChanged }: Props) {
  const [mode, setMode] = useState<Mode>(null);
  const [showHistory, setShowHistory] = useState(false);
  const info = summaryInfo(control.summary_code);
  const component = control.component;
  const hasControlId = control.exists && control.id !== null;

  function done() {
    setMode(null);
    onChanged();
  }

  return (
    <section className="surface-card" data-testid={`sire-control-${component}`} data-summary={control.summary_code} aria-labelledby={`sire-${component}-title`} style={{ padding: "1.1rem", display: "flex", flexDirection: "column", gap: "0.9rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.6rem", flexWrap: "wrap", alignItems: "baseline" }}>
        <h3 id={`sire-${component}-title`} style={{ fontSize: "1.05rem", margin: 0 }}>{COMPONENT_LABEL[component]}</h3>
        <span data-testid={`sire-state-${component}`} style={{ color: TONE[info.tone], fontWeight: 700 }}>{info.label}</span>
      </div>
      <p style={{ color: "var(--muted)", fontSize: "0.85rem", margin: 0 }}>{info.hint}</p>
      {control.review_stale && (
        <div role="alert" data-testid={`sire-stale-${component}`} className="simulated-banner" style={{ borderColor: "var(--warning, #b26a00)" }}>
          Una fuente o la tolerancia cambió después de tu última revisión (v{control.current_review?.version}). La revisión anterior se conserva en el historial; este control necesita una revisión nueva.
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))", gap: "0.7rem" }}>
        {(["SUNAT", "DARWIN"] as SireOrigin[]).map((origin) => (
          <SourceCard key={origin} origin={origin} source={origin === "SUNAT" ? control.sunat_source : control.darwin_source} canWrite={canWrite} component={component} onManual={() => setMode({ kind: "manual", origin })} onImport={() => setMode({ kind: "import", origin })} />
        ))}
      </div>

      {mode?.kind === "manual" && (
        <SireSourceForm clientId={clientId} periodId={periodId} component={component} origin={mode.origin} replaces={mode.origin === "SUNAT" ? control.sunat_source : control.darwin_source} onDone={done} onCancel={() => setMode(null)} />
      )}
      {mode?.kind === "import" && (
        <SireImportWizard clientId={clientId} periodId={periodId} component={component} origin={mode.origin} replaces={mode.origin === "SUNAT" ? control.sunat_source : control.darwin_source} onDone={done} onCancel={() => setMode(null)} />
      )}

      <ComparisonTable control={control} />

      {hasControlId && (
        <>
          <ToleranceBox control={control} canWrite={canWrite} onChanged={onChanged} />
          <EvidenceBox control={control} canWrite={canWrite} onChanged={onChanged} />
          <ReviewBox control={control} canWrite={canWrite} onChanged={onChanged} />
          <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
            <Button type="button" variant="secondary" onClick={() => setShowHistory((v) => !v)} aria-expanded={showHistory}>
              {showHistory ? "Ocultar historial" : `Historial (${control.history_counts.sources + control.history_counts.reviews + control.history_counts.files})`}
            </Button>
            <Link className="btn btn-secondary" href={`/clients/${clientId}/sire-control/${control.id}`}>Reporte de control</Link>
          </div>
          {showHistory && <HistoryList controlId={control.id as string} />}
        </>
      )}
    </section>
  );
}

// --- fuentes -------------------------------------------------------------------------------------------------------------------------

function SourceCard({ origin, source, canWrite, component, onManual, onImport }: { origin: SireOrigin; source: SireSource | null; canWrite: boolean; component: string; onManual: () => void; onImport: () => void }) {
  return (
    <div data-testid={`source-${component}-${origin}`} style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.8rem", display: "flex", flexDirection: "column", gap: "0.35rem" }}>
      <strong>{ORIGIN_LABEL[origin]}</strong>
      {source ? (
        <>
          <span style={{ fontSize: "0.85rem" }}>
            v{source.version} · {source.entry_mode === "IMPORT" ? "importada de un archivo (se conserva con su SHA-256)" : `ingreso manual atribuido a ${origin === "SUNAT" ? "SUNAT" : "DARWIN / CONTASIS"}, sin cotejar con un archivo`} · {source.currency}
          </span>
          <span style={{ fontSize: "0.85rem" }}>{source.source_label} · fuente del {formatDate(source.source_date)}</span>
          <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>Incorporada el {formatDate(source.created_at)}</span>
          <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "0.15rem 0.7rem", fontSize: "0.85rem" }}>
            <dt>Registros</dt><dd style={{ margin: 0 }}>{formatConceptValue("record_count", source.record_count === null ? null : String(source.record_count), source.currency)}</dd>
            <dt>Base</dt><dd style={{ margin: 0 }}>{formatConceptValue("base_amount", source.base_amount, source.currency)}</dd>
            <dt>IGV</dt><dd style={{ margin: 0 }}>{formatConceptValue("igv_amount", source.igv_amount, source.currency)}</dd>
            <dt>Total</dt><dd style={{ margin: 0 }}>{formatConceptValue("total_amount", source.total_amount, source.currency)}</dd>
          </dl>
          {source.notes && <span style={{ fontSize: "0.8rem" }}>Obs.: {source.notes}</span>}
        </>
      ) : (
        <span style={{ color: "var(--muted)", fontSize: "0.85rem" }}>Sin información incorporada.</span>
      )}
      {canWrite && (
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.2rem" }}>
          <Button type="button" variant="secondary" onClick={onManual}>{source ? "Reemplazar (manual)" : "Ingresar datos"}</Button>
          <Button type="button" variant="secondary" onClick={onImport}>{source ? "Reemplazar (archivo)" : "Importar archivo"}</Button>
        </div>
      )}
    </div>
  );
}

// --- comparacion -----------------------------------------------------------------------------------------------------------------------

function ComparisonTable({ control }: { control: SireControl }) {
  const c = control.comparison;
  const currency = control.sunat_source?.currency ?? control.darwin_source?.currency ?? "PEN";
  if (c.status === "NO_SOURCES") return null;
  if (c.concepts.length === 0) {
    return (
      <p role="status" data-testid={`comparison-${control.component}`} style={{ fontSize: "0.9rem", margin: 0 }}>
        {c.single_source
          ? `Comparación pendiente: solo existe la fuente ${c.single_source === "SUNAT" ? "SUNAT" : "DARWIN / CONTASIS"}. Los datos de la otra fuente son desconocidos, no cero.`
          : c.currency_mismatch ? "No comparable: las fuentes están en monedas distintas." : "No comparable: las fuentes no comparten conceptos."}
      </p>
    );
  }
  return (
    <div data-testid={`comparison-${control.component}`} style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.85rem" }}>
        <caption style={{ textAlign: "left", fontWeight: 600, paddingBottom: "0.3rem" }}>Comparación SUNAT vs DARWIN / CONTASIS (diferencia = DARWIN − SUNAT)</caption>
        <thead>
          <tr>{["Concepto", "SUNAT", "DARWIN / CONTASIS", "Diferencia", "Resultado"].map((h) => <th key={h} scope="col" style={{ textAlign: "left", padding: "0.3rem 0.5rem", borderBottom: "1px solid var(--border, #ddd)" }}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {c.concepts.map((r) => (
            <tr key={r.concept} data-testid={`concept-${control.component}-${r.concept}`}>
              <th scope="row" style={{ textAlign: "left", padding: "0.3rem 0.5rem", fontWeight: 500 }}>{CONCEPT_LABEL[r.concept]}</th>
              <td style={{ padding: "0.3rem 0.5rem" }}>{formatConceptValue(r.concept, r.sunat, currency)}</td>
              <td style={{ padding: "0.3rem 0.5rem" }}>{formatConceptValue(r.concept, r.darwin, currency)}</td>
              <td style={{ padding: "0.3rem 0.5rem", fontWeight: 600 }}>{formatDifference(r.difference, r.concept)}</td>
              <td style={{ padding: "0.3rem 0.5rem", color: !r.comparable ? TONE.warn : r.exact ? TONE.ok : r.matches ? TONE.warn : TONE.danger }}>
                {!r.comparable ? "Solo en una fuente (no comparable)" : r.exact ? "Cuadra" : r.within_tolerance ? "Dentro de la tolerancia (diferencia original visible)" : "Difiere"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.tolerance_applied && <p style={{ color: "var(--warning, #b26a00)", fontSize: "0.8rem" }}>Se aplicó una tolerancia explícita de {formatMoney(c.tolerance, currency)}; las diferencias originales se muestran arriba.</p>}
    </div>
  );
}

function ToleranceBox({ control, canWrite, onChanged }: { control: SireControl; canWrite: boolean; onChanged: () => void }) {
  const [amount, setAmount] = useState(control.tolerance_amount ?? "");
  const [reason, setReason] = useState(control.tolerance_reason ?? "");
  const [failure, setFailure] = useState<DescribedError | null>(null);
  const [working, setWorking] = useState(false);
  const busy = useRef(false);
  if (!canWrite && !control.tolerance_amount) return null;

  async function apply(clear: boolean) {
    if (busy.current || control.id === null || control.row_version === null) return;
    busy.current = true;
    setWorking(true);
    setFailure(null);
    try {
      await setTolerance(control.id, { amount: clear ? null : amount.trim() || null, reason: clear ? null : reason.trim() || null, expected_row_version: control.row_version });
      onChanged();
    } catch (err) {
      setFailure(describeSireError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      busy.current = false;
      setWorking(false);
    }
  }

  return (
    <details data-testid={`tolerance-${control.component}`}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        Tolerancia de comparación: {control.tolerance_amount ? `${formatMoney(control.tolerance_amount)} (explícita)` : "exacta (por defecto)"}
      </summary>
      <p style={{ color: "var(--muted)", fontSize: "0.8rem" }}>La comparación es exacta salvo que definas una tolerancia. Es opcional, queda trazada y nunca oculta las diferencias originales; solo aplica a importes.</p>
      {control.tolerance_reason && <p style={{ fontSize: "0.85rem" }}>Motivo: {control.tolerance_reason}</p>}
      {canWrite && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 200px), 1fr))", gap: "0.6rem" }}>
          <Input label="Tolerancia (importe)" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" />
          <Input label="Motivo (obligatorio)" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end" }}>
            <Button type="button" variant="secondary" onClick={() => void apply(false)} disabled={working || amount.trim() === ""}>Aplicar</Button>
            {control.tolerance_amount && <Button type="button" variant="secondary" onClick={() => void apply(true)} disabled={working}>Volver a exacta</Button>}
          </div>
        </div>
      )}
      {failure && <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>{failure.message}</div>}
    </details>
  );
}

// --- evidencias ---------------------------------------------------------------------------------------------------------------------------

function EvidenceBox({ control, canWrite, onChanged }: { control: SireControl; canWrite: boolean; onChanged: () => void }) {
  const [origin, setOrigin] = useState<SireOrigin>("SUNAT");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [inputKey, setInputKey] = useState(0); // reinicia el campo de archivo tras adjuntar (permite volver a elegir el mismo)
  const [failure, setFailure] = useState<DescribedError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [integrity, setIntegrity] = useState<Record<string, SireFileIntegrity>>({});
  const [working, setWorking] = useState(false);
  const busy = useRef(false);
  const files: SireFile[] = control.files.filter((f) => f.status === "ACTIVE");

  async function upload() {
    if (!file || busy.current || control.id === null) return;
    busy.current = true;
    setWorking(true);
    setFailure(null);
    setNotice(null);
    try {
      const result = await attachEvidence({ controlId: control.id, origin, file, description: description.trim() || null });
      setNotice(result.created ? "Evidencia guardada." : "Ese archivo ya estaba en el expediente: no se duplicó.");
      setFile(null);
      setInputKey((k) => k + 1);
      setDescription("");
      onChanged();
    } catch (err) {
      setFailure(describeSireError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      busy.current = false;
      setWorking(false);
    }
  }

  async function check(f: SireFile) {
    if (control.id === null) return;
    try {
      setIntegrity({ ...integrity, [f.id]: await verifyFile(control.id, f.id) });
    } catch {
      setFailure({ message: "No se pudo verificar la integridad del archivo.", rows: [] });
    }
  }

  return (
    <details open={files.length > 0} data-testid={`evidence-${control.component}`}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Evidencias y archivos ({files.length})</summary>
      <p style={{ color: "var(--muted)", fontSize: "0.8rem" }}>Los archivos se conservan fuera del repositorio con su huella SHA-256. Un PDF o una imagen se guarda como respaldo: no se interpreta automáticamente.</p>
      {files.length === 0 ? (
        <p style={{ fontSize: "0.85rem" }}>Aún no hay archivos de respaldo.</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
          {files.map((f) => (
            <li key={f.id} style={{ fontSize: "0.85rem" }}>
              <strong>{f.original_filename}</strong> · {ORIGIN_LABEL[f.origin]} · {f.purpose === "SOURCE_FILE" ? "archivo fuente" : "evidencia"} · {formatBytes(f.size_bytes)} · {formatDate(f.uploaded_at)}
              <br />
              <code title={f.sha256_hash} style={{ fontSize: "0.75rem" }}>SHA-256 {f.sha256_hash.slice(0, 16)}…</code>{" "}
              <a href={control.id ? downloadUrl(control.id, f.id) : "#"} download>Descargar</a>{" "}
              <button type="button" className="btn btn-secondary" style={{ padding: "0.1rem 0.5rem" }} onClick={() => void check(f)}>Verificar integridad</button>
              {integrity[f.id] && (
                <span role="status" style={{ marginLeft: "0.4rem", color: integrity[f.id].intact ? TONE.ok : TONE.danger }}>
                  {integrity[f.id].intact ? "íntegro" : integrity[f.id].present ? "ALTERADO: la huella no coincide" : "archivo ausente"}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {canWrite && (
        <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", alignItems: "flex-end", marginTop: "0.5rem" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.2rem", fontSize: "0.85rem" }}>
            Archivo de respaldo
            <input key={inputKey} type="file" accept=".pdf,.png,.jpg,.jpeg,.csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} data-testid={`evidence-input-${control.component}`} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.2rem", fontSize: "0.85rem" }}>
            Origen
            <select value={origin} onChange={(e) => setOrigin(e.target.value as SireOrigin)} className="select">
              <option value="SUNAT">SUNAT</option>
              <option value="DARWIN">DARWIN / CONTASIS</option>
            </select>
          </label>
          <Input label="Descripción (opcional)" maxLength={300} value={description} onChange={(e) => setDescription(e.target.value)} />
          <Button type="button" variant="secondary" onClick={() => void upload()} disabled={!file || working}>{working ? "Guardando…" : "Adjuntar"}</Button>
        </div>
      )}
      {notice && <p role="status" style={{ fontSize: "0.85rem" }}>{notice}</p>}
      {failure && <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>{failure.message}</div>}
    </details>
  );
}

// --- revision profesional -------------------------------------------------------------------------------------------------------------------

function ReviewBox({ control, canWrite, onChanged }: { control: SireControl; canWrite: boolean; onChanged: () => void }) {
  const decisions = allowedDecisions(control);
  const [decision, setDecision] = useState<SireDecision | "">("");
  const [notes, setNotes] = useState("");
  const [failure, setFailure] = useState<DescribedError | null>(null);
  const [working, setWorking] = useState(false);
  const busy = useRef(false);
  const key = useRef(crypto.randomUUID());
  const review = control.current_review;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy.current || control.id === null || decision === "") return;
    if (decisionNeedsNotes(decision) && notes.trim() === "") {
      setFailure({ message: "Esta decisión exige una observación.", rows: [] });
      return;
    }
    busy.current = true;
    setWorking(true);
    setFailure(null);
    try {
      await recordReview(control.id, { decision, notes: notes.trim() || null, comparison_hash: control.comparison.comparison_hash, idempotency_key: key.current });
      setDecision("");
      setNotes("");
      key.current = crypto.randomUUID();
      onChanged();
    } catch (err) {
      setFailure(describeSireError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      busy.current = false;
      setWorking(false);
    }
  }

  return (
    <div data-testid={`review-${control.component}`} style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.8rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      <strong>Revisión profesional</strong>
      {review ? (
        <p style={{ margin: 0, fontSize: "0.9rem" }} data-testid={`review-current-${control.component}`}>
          v{review.version} · <strong>{DECISION_LABEL[review.decision]}</strong> · {review.reviewed_by_name ?? "usuario"} · {formatDate(review.reviewed_at)}
          {review.notes ? <> · «{review.notes}»</> : null}
          {control.review_stale ? <span style={{ color: TONE.warn }}> · desactualizada</span> : null}
        </p>
      ) : (
        <p style={{ margin: 0, fontSize: "0.9rem", color: "var(--muted)" }}>Sin revisión. Un cuadre numérico no reemplaza tu revisión profesional.</p>
      )}
      {canWrite && decisions.length > 0 && (
        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }} aria-label={`Registrar revisión ${control.component}`}>
          <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", gap: "1rem", flexWrap: "wrap" }}>
            <legend style={{ fontSize: "0.85rem", color: "var(--muted)" }}>{review ? "Nueva revisión (reemplaza la vigente; la anterior queda en el historial)" : "Registrar revisión"}</legend>
            {decisions.map((d) => (
              <label key={d} style={{ display: "flex", gap: "0.35rem", alignItems: "center", fontSize: "0.9rem" }}>
                <input type="radio" name={`decision-${control.component}`} value={d} checked={decision === d} onChange={() => setDecision(d)} />
                {DECISION_LABEL[d]}
              </label>
            ))}
          </fieldset>
          <Input label={decision && decisionNeedsNotes(decision) ? "Observación (obligatoria)" : "Observación (opcional)"} maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} />
          <div><Button type="submit" disabled={decision === "" || working}>{working ? "Guardando…" : "Registrar revisión"}</Button></div>
        </form>
      )}
      {canWrite && decisions.length === 0 && <p style={{ color: "var(--muted)", fontSize: "0.85rem", margin: 0 }}>Incorpora al menos una fuente para poder revisar.</p>}
      {failure && <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>{failure.message}</div>}
    </div>
  );
}

// --- historial ------------------------------------------------------------------------------------------------------------------------------------

function HistoryList({ controlId }: { controlId: string }) {
  const [history, setHistory] = useState<SireHistory | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    void Promise.resolve()
      .then(() => fetchHistory(controlId))
      .then((h) => alive && setHistory(h))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [controlId]);
  if (error) return <p role="alert">No se pudo cargar el historial.</p>;
  if (!history) return <p role="status">Cargando historial…</p>;
  return (
    <ol data-testid="sire-history" style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.35rem" }}>
      {history.items.map((i) => (
        <li key={`${i.kind}-${i.ref_id}`} style={{ fontSize: "0.85rem" }} data-kind={i.kind}>
          <strong>{i.kind === "SOURCE" ? `Fuente ${i.origin} v${i.version}` : i.kind === "REVIEW" ? `Revisión v${i.version}` : `Archivo ${i.origin}`}</strong> · {i.kind === "REVIEW" ? DECISION_LABEL[i.label as SireDecision] ?? i.label : i.label} · {i.status === "ACTIVE" || i.status === "CURRENT" ? "vigente" : i.status === "SUPERSEDED" ? "reemplazada" : i.status} · {formatDate(i.at)}
        </li>
      ))}
    </ol>
  );
}
