// Espejo de `backend/app/schemas/ficha_ruc.py` (Producto M2). Solo datos ESTRUCTURADOS ya validados por el backend:
// nunca HTML, DOM, cookies ni credenciales. El frontend no interpreta la Ficha: solo muestra la propuesta y el diff.

export type FichaReviewStatus = "PENDING_REVIEW" | "ACCEPTED" | "REJECTED" | "SUPERSEDED";
export type FichaPurpose = "ONBOARDING" | "REFRESH";
export type OnboardingStatus = "IN_PROGRESS" | "AWAITING_REVIEW" | "COMPLETED" | "CANCELLED" | "FAILED";

export interface DiffEntry {
  field: string;
  label: string;
  current: unknown;
  proposed: unknown;
  changed: boolean;
}

export interface FichaSnapshot {
  id: string;
  client_id: string;
  session_id: string;
  purpose: FichaPurpose | string;
  source: string;
  schema_version: number;
  retrieved_at: string;
  review_status: FichaReviewStatus | string;
  fields: Record<string, unknown>;
  malformed: string[];
  diff: DiffEntry[];
  accepted_fields: string[] | null;
  reviewed_at: string | null;
  version: number;
}

export interface OfficialRegime {
  value: string | null;
  label: string | null;
  source: string | null;
  as_of: string | null;
  snapshot_id: string | null;
}

// «Información SUNAT»: ultimo snapshot ACEPTADO con su fecha. `official_regime.value === null` => NO confirmado por SUNAT.
export interface FichaOverview {
  client_id: string;
  official_regime: OfficialRegime;
  internal_tax_regime: string;
  internal_regime_source: "MANUAL" | "SUNAT_FICHA_RUC" | "NO_REGISTRADO" | string;
  internal_regime_changed_at: string | null;
  latest_accepted: FichaSnapshot | null;
  pending_snapshot_id: string | null;
  last_verified_at: string | null;
  last_unchanged_verification_at: string | null;
  sensitive_sections_visible: boolean;
}

export interface Onboarding {
  id: string;
  client_id: string;
  ruc: string;
  status: OnboardingStatus | string;
  snapshot_id: string | null;
  created_at: string;
  completed_at: string | null;
  version: number;
}

export interface CompleteOnboardingPayload {
  accepted_fields: string[];
  overrides: Record<string, string>;
  activate: boolean;
  expected_version: number;
}
