"use client";

// Importacion de CSV/XLSX en dos pasos: (1) subir -> vista previa (hojas, encabezados, muestra, estilo decimal detectado); (2) el contador ASIGNA cada campo a una columna
// y confirma. No se presume ningun formato oficial de SUNAT ni de DARWIN. El archivo original se conserva con su SHA-256; nada se importa si alguna fila es invalida.
import { useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { commitImport, previewImportFile } from "@/lib/sireControl";
import { type DescribedError, ORIGIN_LABEL, describeSireError, formatBytes } from "@/lib/sireControlModel";
import type { SireComponent, SireControl, SireCurrency, SireImportMapping, SireOrigin, SirePreview, SireSource } from "@/lib/types/sireControl";

interface Props {
  clientId: string;
  periodId: string;
  component: SireComponent;
  origin: SireOrigin;
  replaces: SireSource | null;
  onDone: (control: SireControl) => void;
  onCancel: () => void;
}

const FIELD_LABELS: Array<[keyof SireImportMapping, string, string]> = [
  ["base_amount", "Base / importe comparable", "Se suma por fila."],
  ["igv_amount", "IGV", "Opcional."],
  ["total_amount", "Importe total", "Opcional."],
  ["record_count", "Cantidad de registros", "Solo en modo resumen."],
  ["period", "Período", "Opcional: se valida contra el período del expediente."],
  ["record_type", "Tipo de registro", "Opcional: se valida contra RVIE/RCE."],
];

const EMPTY_MAPPING: SireImportMapping = { record_count: null, base_amount: null, igv_amount: null, total_amount: null, period: null, record_type: null };

function todayLima(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Lima" }).format(new Date());
}

export function SireImportWizard({ clientId, periodId, component, origin, replaces, onDone, onCancel }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SirePreview | null>(null);
  const [mapping, setMapping] = useState<SireImportMapping>(EMPTY_MAPPING);
  const [mode, setMode] = useState<"DETAIL" | "SUMMARY">("DETAIL");
  const [decimalStyle, setDecimalStyle] = useState<"AUTO" | "DOT" | "COMMA">("AUTO");
  const [ignoreTotals, setIgnoreTotals] = useState(false);
  const [sheet, setSheet] = useState<string | null>(null);
  const [currency, setCurrency] = useState<SireCurrency>("PEN");
  const [label, setLabel] = useState("");
  const [sourceDate, setSourceDate] = useState(todayLima());
  const [notes, setNotes] = useState("");
  const [failure, setFailure] = useState<DescribedError | null>(null);
  const [working, setWorking] = useState(false);
  const busy = useRef(false);
  const key = useRef(crypto.randomUUID());

  async function runPreview(chosenSheet?: string | null) {
    if (!file || busy.current) return;
    busy.current = true;
    setWorking(true);
    setFailure(null);
    try {
      const result = await previewImportFile({ clientId, periodId, component, origin, file, sheet: chosenSheet ?? sheet });
      setPreview(result);
      setSheet(result.sheet);
      setMapping({ ...EMPTY_MAPPING, ...Object.fromEntries(Object.entries(result.suggested_mapping).filter(([, v]) => v)) } as SireImportMapping);
      setDecimalStyle(result.decimal_style_detected ?? "AUTO");
      if (!label) setLabel(file.name.slice(0, 120));
    } catch (err) {
      setPreview(null);
      setFailure(describeSireError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      busy.current = false;
      setWorking(false);
    }
  }

  async function runImport() {
    if (!preview || busy.current) return;
    busy.current = true;
    setWorking(true);
    setFailure(null);
    try {
      const result = await commitImport({
        file_id: preview.file.id,
        mode,
        decimal_style: decimalStyle,
        mapping,
        ignore_total_rows: ignoreTotals,
        sheet: preview.sheet,
        encoding: null,
        delimiter: null,
        currency,
        source_label: label.trim(),
        source_date: sourceDate,
        notes: notes.trim() === "" ? null : notes.trim(),
        replaces_source_id: replaces?.id ?? null,
        idempotency_key: key.current,
      });
      onDone(result.control);
    } catch (err) {
      setFailure(describeSireError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      busy.current = false;
      setWorking(false);
    }
  }

  const needsDecimalChoice = Boolean(preview?.decimal_style_needs_choice) && decimalStyle === "AUTO";
  const canImport = Boolean(preview) && label.trim() !== "" && !needsDecimalChoice && Object.values(mapping).some(Boolean) && !working;

  return (
    <section aria-label={`Importar archivo ${component} ${origin}`} data-testid={`import-wizard-${component}-${origin}`} style={{ display: "flex", flexDirection: "column", gap: "0.7rem", border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.9rem" }}>
      <strong>{replaces ? "Reemplazar" : "Importar"} archivo · {ORIGIN_LABEL[origin]} · {component}</strong>
      <p style={{ color: "var(--muted)", fontSize: "0.85rem", margin: 0 }}>
        CSV o XLSX (sin macros ni fórmulas). Primero verás una vista previa; luego eliges qué columna corresponde a cada dato. El archivo original se conserva con su huella SHA-256.
      </p>
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
          Archivo (CSV o XLSX)
          <input type="file" accept=".csv,.xlsx,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setFailure(null); setSheet(null); }} data-testid="import-file-input" />
        </label>
        <Button type="button" onClick={() => void runPreview(null)} disabled={!file || working}>{working && !preview ? "Leyendo…" : "Ver vista previa"}</Button>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={working}>Cancelar</Button>
      </div>
      {failure && (
        <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>
          {failure.message}
          {failure.rows.length > 0 && <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>{failure.rows.map((r) => <li key={r}>{r}</li>)}</ul>}
        </div>
      )}
      {preview && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.7rem" }} data-testid="import-preview">
          <p style={{ fontSize: "0.85rem", margin: 0 }} role="status">
            {preview.file.original_filename} · {formatBytes(preview.file.size_bytes)} · {preview.kind.toUpperCase()}
            {preview.delimiter ? ` · delimitador «${preview.delimiter === "\t" ? "tab" : preview.delimiter}»` : ""}
            {preview.encoding ? ` · ${preview.encoding}` : ""} · SHA-256 {preview.file.sha256_hash.slice(0, 12)}…
            {preview.duplicate_of_existing && " · este archivo ya estaba cargado en el expediente"}
          </p>
          {preview.sheets.length > 1 && (
            <Select label="Hoja del libro" options={preview.sheets.map((s) => ({ value: s, label: s }))} value={sheet ?? preview.sheets[0]} onChange={(e) => { setSheet(e.target.value); void runPreview(e.target.value); }} />
          )}
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", fontSize: "0.8rem", minWidth: "100%" }} aria-label="Vista previa del archivo">
              <thead><tr>{preview.headers.map((h) => <th key={h} scope="col" style={{ textAlign: "left", padding: "0.25rem 0.5rem", borderBottom: "1px solid var(--border, #ddd)" }}>{h}</th>)}</tr></thead>
              <tbody>
                {preview.sample_rows.slice(0, 5).map((row, i) => (
                  <tr key={i}>{row.map((cell, j) => <td key={j} style={{ padding: "0.2rem 0.5rem", borderBottom: "1px solid var(--border, #eee)" }}>{cell}</td>)}</tr>
                ))}
              </tbody>
            </table>
            {preview.truncated && <p style={{ color: "var(--muted)", fontSize: "0.8rem", margin: "0.25rem 0 0" }}>Se muestran las primeras filas.</p>}
          </div>
          <fieldset style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.6rem" }}>
            <legend style={{ fontWeight: 600 }}>Asignación de columnas</legend>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: "0.6rem" }}>
              {FIELD_LABELS.map(([field, text, hint]) => (
                <div key={field}>
                  <Select
                    label={text}
                    options={preview.headers.map((h) => ({ value: h, label: h }))}
                    placeholder="— no asignado —"
                    value={mapping[field] ?? ""}
                    onChange={(e) => setMapping({ ...mapping, [field]: e.target.value || null })}
                  />
                  <span style={{ color: "var(--muted)", fontSize: "0.75rem" }}>{hint}</span>
                </div>
              ))}
            </div>
          </fieldset>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: "0.6rem" }}>
            <Select label="Contenido del archivo" options={[{ value: "DETAIL", label: "Detalle (una fila por comprobante)" }, { value: "SUMMARY", label: "Resumen (una sola fila de totales)" }]} value={mode} onChange={(e) => setMode(e.target.value as "DETAIL" | "SUMMARY")} />
            <Select
              label="Separador decimal de los importes"
              options={[{ value: "AUTO", label: "Detectar" }, { value: "DOT", label: "Punto (1,234.56)" }, { value: "COMMA", label: "Coma (1.234,56)" }]}
              value={decimalStyle}
              onChange={(e) => setDecimalStyle(e.target.value as "AUTO" | "DOT" | "COMMA")}
            />
            <Select label="Moneda" options={[{ value: "PEN", label: "Soles (PEN)" }, { value: "USD", label: "Dólares (USD)" }]} value={currency} onChange={(e) => setCurrency(e.target.value as SireCurrency)} />
          </div>
          {needsDecimalChoice && <p role="alert" style={{ color: "var(--danger, #c0392b)", fontSize: "0.85rem", margin: 0 }}>Hay importes ambiguos (p. ej. 1,234): elige punto o coma como separador decimal.</p>}
          <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", fontSize: "0.85rem" }}>
            <input type="checkbox" checked={ignoreTotals} onChange={(e) => setIgnoreTotals(e.target.checked)} />
            Mi archivo incluye filas de totales y confirmo ignorarlas (por defecto se rechazan)
          </label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: "0.6rem" }}>
            <Input label="Identificación de la fuente" maxLength={120} value={label} onChange={(e) => setLabel(e.target.value)} />
            <Input label="Fecha de la fuente" type="date" max={todayLima()} value={sourceDate} onChange={(e) => setSourceDate(e.target.value)} />
          </div>
          <Input label="Observaciones (opcional)" maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} />
          <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
            <Button type="button" onClick={() => void runImport()} disabled={!canImport}>{working ? "Importando…" : replaces ? "Importar como nueva versión" : "Importar como fuente"}</Button>
          </div>
        </div>
      )}
    </section>
  );
}
