export type PersonType = "natural" | "juridica";

export type RucStatus =
  | "ACTIVO"
  | "BAJA_PROVISIONAL"
  | "BAJA_DEFINITIVA"
  | "BAJA_DE_OFICIO"
  | "SUSPENSION_TEMPORAL";

export type FiscalAddressCondition = "HABIDO" | "NO_HABIDO" | "NO_HALLADO" | "PENDIENTE";

export type TaxRegime = "NRUS" | "RER" | "RMT" | "GENERAL";

export type IssuanceSystem = "manual" | "electronico" | "mixto";

export type ServiceStatus = "prospecto" | "activo" | "suspendido" | "pendiente_cierre" | "inactivo";

export interface ClientBase {
  ruc: string;
  business_name: string;
  trade_name: string | null;
  person_type: PersonType;
  taxpayer_type: string | null;
  ruc_status: RucStatus;
  fiscal_address_condition: FiscalAddressCondition;
  fiscal_address: string | null;
  ubigeo_code: string | null;
  department: string | null;
  province: string | null;
  district: string | null;
  main_economic_activity: string | null;
  secondary_economic_activities: string[] | null;
  tax_regime: TaxRegime;
  activities_start_date: string | null;
  issuance_system: IssuanceSystem | null;
  phone: string | null;
  email: string | null;
  legal_representative_name: string | null;
  legal_representative_doc: string | null;
  contact_person: string | null;
  whatsapp_number: string | null;
  monthly_fee: string | null;
  payment_day: number | null;
  onboarding_date: string | null;
  notes: string | null;
  tags: string[] | null;
}

export interface Client extends ClientBase {
  id: string;
  tenant_id: string;
  ruc_last_digit: number | null;
  service_status: ServiceStatus;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
  // Calculado dinámicamente por el backend desde client_tax_categories
  // (ver ADR-0013); no se envía en creación/edición.
  is_good_taxpayer: boolean;
}

export interface ClientListResponse {
  items: Client[];
  total: number;
  limit: number;
  offset: number;
}

export interface RucLookupResult {
  ruc: string;
  business_name: string;
  trade_name: string | null;
  taxpayer_type: string | null;
  ruc_status: string;
  fiscal_address_condition: string;
  fiscal_address: string | null;
  ubigeo_code: string | null;
  department: string | null;
  province: string | null;
  district: string | null;
  main_economic_activity: string | null;
  secondary_economic_activities: string[];
  queried_at: string;
  is_simulated: boolean;
}
