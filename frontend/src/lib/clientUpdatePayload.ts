import type { ClientBase, ClientUpdatePayload } from "@/lib/types/client";

/**
 * Construye el payload de PUT /api/v1/clients/{id} mediante mapeo explícito,
 * campo por campo -- nunca reenvía el objeto de lectura completo (`Client`)
 * ni usa spread sobre él. Esto es lo único que garantiza que campos de solo
 * lectura (id, tenant_id, ruc_last_digit, service_status, last_verified_at,
 * created_at, updated_at, is_good_taxpayer) nunca lleguen al request, sin
 * depender de que el estado interno del formulario se mantenga "limpio".
 *
 * Normalización de cadenas opcionales: "" y espacios en blanco se tratan como
 * ausencia de valor y se envían como `null` -- el backend no distingue una
 * cadena vacía de un campo no informado para estos campos (ninguno tiene una
 * restricción que dependa de "" vs `null`), y el formulario ya usa `null`
 * como su representación de "vacío" en cada input opcional.
 */
export function buildClientUpdatePayload(form: ClientBase): ClientUpdatePayload {
  return {
    ruc: form.ruc,
    business_name: form.business_name,
    trade_name: normalizeOptionalString(form.trade_name),
    person_type: form.person_type,
    taxpayer_type: normalizeOptionalString(form.taxpayer_type),
    ruc_status: form.ruc_status,
    fiscal_address_condition: form.fiscal_address_condition,
    fiscal_address: normalizeOptionalString(form.fiscal_address),
    ubigeo_code: normalizeOptionalString(form.ubigeo_code),
    department: normalizeOptionalString(form.department),
    province: normalizeOptionalString(form.province),
    district: normalizeOptionalString(form.district),
    main_economic_activity: normalizeOptionalString(form.main_economic_activity),
    secondary_economic_activities: form.secondary_economic_activities,
    tax_regime: form.tax_regime,
    activities_start_date: form.activities_start_date,
    issuance_system: form.issuance_system,
    phone: normalizeOptionalString(form.phone),
    email: normalizeOptionalString(form.email),
    legal_representative_name: normalizeOptionalString(form.legal_representative_name),
    legal_representative_doc: normalizeOptionalString(form.legal_representative_doc),
    contact_person: normalizeOptionalString(form.contact_person),
    whatsapp_number: normalizeOptionalString(form.whatsapp_number),
    monthly_fee: normalizeOptionalString(form.monthly_fee),
    payment_day: form.payment_day,
    onboarding_date: form.onboarding_date,
    notes: normalizeOptionalString(form.notes),
    tags: form.tags,
  };
}

function normalizeOptionalString(value: string | null): string | null {
  if (value === null || value === undefined) return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : value;
}
