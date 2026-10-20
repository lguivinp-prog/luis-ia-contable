"use client";

// Registro estructurado MANUAL de una fuente (SUNAT o DARWIN/CONTASIS) de un componente. Son datos de CONTROL, no determinaciones tributarias: un campo vacio queda como
// DESCONOCIDO (nunca cero). Un solo envio a la vez; el reintento por doble clic es idempotente (misma clave).
import { useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { addManualSource } from "@/lib/sireControl";
import {
  COMPONENT_LABEL,
  type DescribedError,
  type FieldErrors,
  ORIGIN_LABEL,
  describeSireError,
  hasErrors,
  normalizeAmountInput,
  validateManualForm,
} from "@/lib/sireControlModel";
import type { SireComponent, SireControl, SireCurrency, SireOrigin, SireSource } from "@/lib/types/sireControl";

interface Props {
  clientId: string;
  periodId: string;
  component: SireComponent;
  origin: SireOrigin;
  replaces: SireSource | null;
  onDone: (control: SireControl) => void;
  onCancel: () => void;
}

function todayLima(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Lima" }).format(new Date());
}

export function SireSourceForm({ clientId, periodId, component, origin, replaces, onDone, onCancel }: Props) {
  const [recordCount, setRecordCount] = useState(replaces?.record_count?.toString() ?? "");
  const [baseAmount, setBaseAmount] = useState(replaces?.base_amount ?? "");
  const [igvAmount, setIgvAmount] = useState(replaces?.igv_amount ?? "");
  const [totalAmount, setTotalAmount] = useState(replaces?.total_amount ?? "");
  const [currency, setCurrency] = useState<SireCurrency>(replaces?.currency ?? "PEN");
  const [sourceLabel, setSourceLabel] = useState("");
  const [sourceDate, setSourceDate] = useState(todayLima());
  const [notes, setNotes] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<DescribedError | null>(null);
  const [working, setWorking] = useState(false);
  const submitting = useRef(false);
  const key = useRef(crypto.randomUUID()); // una intencion = una clave: el doble clic o el reintento no duplican la fuente

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const found = validateManualForm({ recordCount, baseAmount, igvAmount, totalAmount, sourceLabel, sourceDate, notes }, todayLima());
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) return;
    submitting.current = true;
    setWorking(true);
    try {
      const result = await addManualSource({
        client_id: clientId,
        tax_period_id: periodId,
        component,
        origin,
        currency,
        record_count: recordCount.trim() === "" ? null : Number(recordCount.trim()),
        base_amount: normalizeAmountInput(baseAmount),
        igv_amount: normalizeAmountInput(igvAmount),
        total_amount: normalizeAmountInput(totalAmount),
        source_label: sourceLabel.trim(),
        source_date: sourceDate,
        notes: notes.trim() === "" ? null : notes.trim(),
        replaces_source_id: replaces?.id ?? null,
        idempotency_key: key.current,
      });
      onDone(result.control);
    } catch (err) {
      setFailure(describeSireError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      submitting.current = false;
      setWorking(false);
    }
  }

  const heading = `${replaces ? "Reemplazar" : "Incorporar"} fuente ${origin === "SUNAT" ? "SUNAT" : "DARWIN / CONTASIS"} · ${component}`;
  return (
    <form onSubmit={submit} noValidate aria-label={heading} data-testid={`source-form-${component}-${origin}`} style={{ display: "flex", flexDirection: "column", gap: "0.7rem", border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.9rem" }}>
      <strong>{heading}</strong>
      <p style={{ color: "var(--muted)", fontSize: "0.85rem", margin: 0 }}>
        {COMPONENT_LABEL[component]} · {ORIGIN_LABEL[origin]}. Ingresa lo que figura en tu fuente. Lo que dejes vacío queda como <strong>desconocido</strong>, no como cero.
      </p>
      {replaces && <p role="note" style={{ fontSize: "0.85rem", margin: 0 }}>Crea una nueva versión (la v{replaces.version} queda en el historial). Si ya había una revisión, tendrá que revisarse de nuevo.</p>}
      {errors.form && <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>{errors.form}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 180px), 1fr))", gap: "0.6rem" }}>
        <Field error={errors.recordCount}><Input label="Cantidad de registros" inputMode="numeric" value={recordCount} onChange={(e) => setRecordCount(e.target.value)} aria-invalid={Boolean(errors.recordCount)} /></Field>
        <Field error={errors.baseAmount}><Input label="Base / importe comparable" inputMode="decimal" value={baseAmount} onChange={(e) => setBaseAmount(e.target.value)} placeholder="0.00" aria-invalid={Boolean(errors.baseAmount)} /></Field>
        <Field error={errors.igvAmount}><Input label="IGV (si corresponde)" inputMode="decimal" value={igvAmount} onChange={(e) => setIgvAmount(e.target.value)} placeholder="0.00" aria-invalid={Boolean(errors.igvAmount)} /></Field>
        <Field error={errors.totalAmount}><Input label="Importe total" inputMode="decimal" value={totalAmount} onChange={(e) => setTotalAmount(e.target.value)} placeholder="0.00" aria-invalid={Boolean(errors.totalAmount)} /></Field>
        <Select label="Moneda" options={[{ value: "PEN", label: "Soles (PEN)" }, { value: "USD", label: "Dólares (USD)" }]} value={currency} onChange={(e) => setCurrency(e.target.value as SireCurrency)} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: "0.6rem" }}>
        <Field error={errors.sourceLabel}><Input label="Identificación de la fuente" maxLength={120} value={sourceLabel} onChange={(e) => setSourceLabel(e.target.value)} placeholder="Ej.: Reporte RVIE septiembre" aria-invalid={Boolean(errors.sourceLabel)} /></Field>
        <Field error={errors.sourceDate}><Input label="Fecha de la fuente" type="date" max={todayLima()} value={sourceDate} onChange={(e) => setSourceDate(e.target.value)} aria-invalid={Boolean(errors.sourceDate)} /></Field>
      </div>
      <Field error={errors.notes}><Input label="Observaciones (opcional)" maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      {failure && (
        <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>
          {failure.message}
        </div>
      )}
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <Button type="submit" disabled={working}>{working ? "Guardando…" : replaces ? "Guardar nueva versión" : "Guardar fuente"}</Button>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={working}>Cancelar</Button>
      </div>
    </form>
  );
}

function Field({ error, children }: { error?: string; children: React.ReactNode }) {
  return (
    <div>
      {children}
      {error && <div role="alert" style={{ color: "var(--danger, #c0392b)", fontSize: "0.8rem" }}>{error}</div>}
    </div>
  );
}
