"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { RucLookupPanel } from "@/components/clients/RucLookupPanel";
import type { ClientBase } from "@/lib/types/client";
import { isValidRuc } from "@/lib/validators/ruc";

const PERSON_TYPE_OPTIONS = [
  { value: "natural", label: "Persona natural" },
  { value: "juridica", label: "Persona jurídica" },
];

const RUC_STATUS_OPTIONS = [
  { value: "ACTIVO", label: "Activo" },
  { value: "BAJA_PROVISIONAL", label: "Baja provisional" },
  { value: "BAJA_DEFINITIVA", label: "Baja definitiva" },
  { value: "BAJA_DE_OFICIO", label: "Baja de oficio" },
  { value: "SUSPENSION_TEMPORAL", label: "Suspensión temporal" },
];

const FISCAL_ADDRESS_CONDITION_OPTIONS = [
  { value: "HABIDO", label: "Habido" },
  { value: "NO_HABIDO", label: "No habido" },
  { value: "NO_HALLADO", label: "No hallado" },
  { value: "PENDIENTE", label: "Pendiente de verificar" },
];

const TAX_REGIME_OPTIONS = [
  { value: "NRUS", label: "NRUS" },
  { value: "RER", label: "RER" },
  { value: "RMT", label: "RMT" },
  { value: "GENERAL", label: "General" },
];

const DEFAULT_VALUES: ClientBase = {
  ruc: "",
  business_name: "",
  trade_name: null,
  person_type: "juridica",
  taxpayer_type: null,
  ruc_status: "ACTIVO",
  fiscal_address_condition: "HABIDO",
  fiscal_address: null,
  ubigeo_code: null,
  department: null,
  province: null,
  district: null,
  main_economic_activity: null,
  secondary_economic_activities: null,
  tax_regime: "RMT",
  activities_start_date: null,
  issuance_system: null,
  phone: null,
  email: null,
  legal_representative_name: null,
  legal_representative_doc: null,
  contact_person: null,
  whatsapp_number: null,
  is_good_taxpayer: false,
  monthly_fee: null,
  payment_day: null,
  onboarding_date: null,
  notes: null,
  tags: null,
};

interface ClientFormProps {
  initialValues?: ClientBase;
  onSubmit: (values: ClientBase) => Promise<void>;
  submitLabel: string;
  lockRuc?: boolean;
}

export function ClientForm({ initialValues, onSubmit, submitLabel, lockRuc }: ClientFormProps) {
  const [values, setValues] = useState<ClientBase>(initialValues ?? DEFAULT_VALUES);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function update<K extends keyof ClientBase>(key: K, value: ClientBase[K]) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!isValidRuc(values.ruc)) {
      setError("El RUC ingresado no tiene un dígito verificador válido.");
      return;
    }

    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch {
      setError("No se pudo guardar el contribuyente. Revisa los datos e intenta nuevamente.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1.25rem", maxWidth: "640px" }}>
      {error && <div className="error-banner">{error}</div>}

      <div style={{ display: "flex", gap: "1rem", alignItems: "flex-end" }}>
        <Input
          label="RUC"
          name="ruc"
          required
          maxLength={11}
          disabled={lockRuc}
          value={values.ruc}
          onChange={(event) => update("ruc", event.target.value.replace(/\D/g, ""))}
        />
        {!lockRuc && <RucLookupPanel ruc={values.ruc} onResult={(result) => {
          update("business_name", result.business_name);
          update("fiscal_address", result.fiscal_address);
          update("ruc_status", result.ruc_status as ClientBase["ruc_status"]);
          update("fiscal_address_condition", result.fiscal_address_condition as ClientBase["fiscal_address_condition"]);
          update("main_economic_activity", result.main_economic_activity);
        }} />}
      </div>

      <Input
        label="Razón social"
        name="business_name"
        required
        value={values.business_name}
        onChange={(event) => update("business_name", event.target.value)}
      />
      <Input
        label="Nombre comercial"
        name="trade_name"
        value={values.trade_name ?? ""}
        onChange={(event) => update("trade_name", event.target.value || null)}
      />

      <div style={{ display: "flex", gap: "1rem" }}>
        <Select
          label="Tipo de contribuyente"
          name="person_type"
          options={PERSON_TYPE_OPTIONS}
          value={values.person_type}
          onChange={(event) => update("person_type", event.target.value as ClientBase["person_type"])}
        />
        <Select
          label="Régimen tributario"
          name="tax_regime"
          options={TAX_REGIME_OPTIONS}
          value={values.tax_regime}
          onChange={(event) => update("tax_regime", event.target.value as ClientBase["tax_regime"])}
        />
      </div>

      <div style={{ display: "flex", gap: "1rem" }}>
        <Select
          label="Estado del RUC"
          name="ruc_status"
          options={RUC_STATUS_OPTIONS}
          value={values.ruc_status}
          onChange={(event) => update("ruc_status", event.target.value as ClientBase["ruc_status"])}
        />
        <Select
          label="Condición del domicilio fiscal"
          name="fiscal_address_condition"
          options={FISCAL_ADDRESS_CONDITION_OPTIONS}
          value={values.fiscal_address_condition}
          onChange={(event) =>
            update("fiscal_address_condition", event.target.value as ClientBase["fiscal_address_condition"])
          }
        />
      </div>

      <Input
        label="Domicilio fiscal"
        name="fiscal_address"
        value={values.fiscal_address ?? ""}
        onChange={(event) => update("fiscal_address", event.target.value || null)}
      />

      <div style={{ display: "flex", gap: "1rem" }}>
        <Input
          label="Teléfono"
          name="phone"
          value={values.phone ?? ""}
          onChange={(event) => update("phone", event.target.value || null)}
        />
        <Input
          label="Correo"
          name="email"
          type="email"
          value={values.email ?? ""}
          onChange={(event) => update("email", event.target.value || null)}
        />
        <Input
          label="WhatsApp"
          name="whatsapp_number"
          value={values.whatsapp_number ?? ""}
          onChange={(event) => update("whatsapp_number", event.target.value || null)}
        />
      </div>

      <div style={{ display: "flex", gap: "1rem" }}>
        <Input
          label="Honorario mensual (S/)"
          name="monthly_fee"
          inputMode="decimal"
          value={values.monthly_fee ?? ""}
          onChange={(event) => update("monthly_fee", event.target.value || null)}
        />
        <Input
          label="Día de cobro (1-31)"
          name="payment_day"
          type="number"
          min={1}
          max={31}
          value={values.payment_day ?? ""}
          onChange={(event) => update("payment_day", event.target.value ? Number(event.target.value) : null)}
        />
      </div>

      <div className="field">
        <label htmlFor="notes">Observaciones</label>
        <textarea
          id="notes"
          className="input"
          rows={3}
          value={values.notes ?? ""}
          onChange={(event) => update("notes", event.target.value || null)}
        />
      </div>

      <Button type="submit" disabled={submitting}>
        {submitting ? "Guardando…" : submitLabel}
      </Button>
    </form>
  );
}
