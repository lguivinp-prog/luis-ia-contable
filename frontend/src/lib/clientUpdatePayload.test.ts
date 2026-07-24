import { describe, expect, it } from "vitest";

import { buildClientUpdatePayload } from "@/lib/clientUpdatePayload";
import type { Client, ClientBase } from "@/lib/types/client";

const VALID_CLIENT_BASE: ClientBase = {
  ruc: "20600000021",
  business_name: "Comercializadora Demo Andina E.I.R.L.",
  trade_name: "Comercializadora Demo",
  person_type: "juridica",
  taxpayer_type: null,
  ruc_status: "ACTIVO",
  fiscal_address_condition: "HABIDO",
  fiscal_address: "Av. Ficticia 123, Lima",
  ubigeo_code: null,
  department: null,
  province: null,
  district: null,
  main_economic_activity: null,
  secondary_economic_activities: null,
  tax_regime: "RER",
  activities_start_date: null,
  issuance_system: null,
  phone: "999888777",
  email: "contacto@demo-ficticio.dev",
  legal_representative_name: null,
  legal_representative_doc: null,
  contact_person: null,
  whatsapp_number: null,
  monthly_fee: "250.00",
  payment_day: 5,
  onboarding_date: null,
  notes: null,
  tags: null,
};

// El objeto de lectura completo (con campos de solo lectura) que el
// formulario recibe desde GET /clients/{id} -- simula exactamente el bug
// original: `initialValues={client}` inyectaba estos campos extra al estado
// interno del formulario, tipado como `ClientBase` pero conteniendo en
// tiempo de ejecucion todas las propiedades de `Client`.
const FULL_CLIENT_WITH_READONLY_FIELDS: Client = {
  ...VALID_CLIENT_BASE,
  id: "3441b255-13f5-441f-a0ba-48db13b41157",
  tenant_id: "975b3161-d119-4bff-82ad-05804150bb0c",
  ruc_last_digit: 1,
  service_status: "activo",
  last_verified_at: "2026-07-20T10:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  is_good_taxpayer: true,
};

const READ_ONLY_KEYS = [
  "id",
  "tenant_id",
  "ruc_last_digit",
  "service_status",
  "last_verified_at",
  "created_at",
  "updated_at",
  "is_good_taxpayer",
] as const;

describe("buildClientUpdatePayload", () => {
  it("acepta un objeto de lectura completo con campos adicionales sin fallar", () => {
    expect(() => buildClientUpdatePayload(FULL_CLIENT_WITH_READONLY_FIELDS)).not.toThrow();
  });

  it("devuelve unicamente los campos permitidos por el schema de actualizacion", () => {
    const payload = buildClientUpdatePayload(FULL_CLIENT_WITH_READONLY_FIELDS);
    const expectedKeys = Object.keys(VALID_CLIENT_BASE).sort();
    expect(Object.keys(payload).sort()).toEqual(expectedKeys);
  });

  it("nunca incluye campos de solo lectura, aunque esten presentes en la entrada", () => {
    const payload = buildClientUpdatePayload(FULL_CLIENT_WITH_READONLY_FIELDS);
    for (const key of READ_ONLY_KEYS) {
      expect(payload).not.toHaveProperty(key);
    }
  });

  it("conserva un campo editable modificado", () => {
    const edited = { ...FULL_CLIENT_WITH_READONLY_FIELDS, business_name: "Nueva Razon Social S.A.C." };
    const payload = buildClientUpdatePayload(edited);
    expect(payload.business_name).toBe("Nueva Razon Social S.A.C.");
  });

  it("normaliza cadenas vacias a null en campos opcionales", () => {
    const cleared = { ...FULL_CLIENT_WITH_READONLY_FIELDS, trade_name: "", phone: "   " };
    const payload = buildClientUpdatePayload(cleared);
    expect(payload.trade_name).toBeNull();
    expect(payload.phone).toBeNull();
  });

  it("conserva null explicito en campos opcionales", () => {
    const payload = buildClientUpdatePayload(FULL_CLIENT_WITH_READONLY_FIELDS);
    expect(payload.taxpayer_type).toBeNull();
  });

  it("conserva un valor no vacio sin alterarlo", () => {
    const payload = buildClientUpdatePayload(FULL_CLIENT_WITH_READONLY_FIELDS);
    expect(payload.fiscal_address).toBe("Av. Ficticia 123, Lima");
    expect(payload.monthly_fee).toBe("250.00");
  });

  it("conserva los campos requeridos (ruc, business_name, person_type, ruc_status, fiscal_address_condition, tax_regime)", () => {
    const payload = buildClientUpdatePayload(FULL_CLIENT_WITH_READONLY_FIELDS);
    expect(payload.ruc).toBe(VALID_CLIENT_BASE.ruc);
    expect(payload.business_name).toBe(VALID_CLIENT_BASE.business_name);
    expect(payload.person_type).toBe(VALID_CLIENT_BASE.person_type);
    expect(payload.ruc_status).toBe(VALID_CLIENT_BASE.ruc_status);
    expect(payload.fiscal_address_condition).toBe(VALID_CLIENT_BASE.fiscal_address_condition);
    expect(payload.tax_regime).toBe(VALID_CLIENT_BASE.tax_regime);
  });
});
