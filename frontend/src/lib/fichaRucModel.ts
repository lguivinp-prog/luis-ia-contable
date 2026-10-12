// Modelo PURO (sin JSX ni red) de la revision de la Ficha RUC y del alta asistida (Producto M2).
// El backend es la autoridad: aqui solo se presenta la propuesta/diff, se construyen payloads de aceptacion y se traducen
// errores a mensajes saneados. La Ficha NUNCA se escribe en el cliente sin una aceptacion humana explicita.
import type { SolAccessSessionOut } from "@/lib/types/solAccess";
import type { DiffEntry, FichaOverview, FichaSnapshot, Onboarding } from "@/lib/types/fichaRuc";

// Espejo de `OVERRIDABLE_FIELDS` del backend: correcciones humanas permitidas (solo textos oficiales).
export const OVERRIDABLE_FIELDS: readonly string[] = [
  "business_name",
  "trade_name",
  "fiscal_address",
  "taxpayer_type",
  "main_economic_activity",
];

export function canOverrideField(field: string): boolean {
  return OVERRIDABLE_FIELDS.includes(field);
}

export function changedEntries(diff: DiffEntry[]): DiffEntry[] {
  return diff.filter((entry) => entry.changed);
}

// Por defecto NADA aprobado automaticamente a ciegas: la persona marca lo que acepta (se ofrece "seleccionar todo").
export function allChangedFields(diff: DiffEntry[]): string[] {
  return changedEntries(diff).map((entry) => entry.field);
}

export const TAX_REGIME_LABEL: Record<string, string> = {
  RMT: "Régimen MYPE Tributario (RMT)",
  GENERAL: "Régimen General",
  RER: "Régimen Especial de Renta (RER)",
  NRUS: "Nuevo RUS (NRUS)",
  SIN_DETERMINAR: "Sin determinar",
};

const ENUM_LABELS: Record<string, Record<string, string>> = {
  tax_regime: TAX_REGIME_LABEL,
  person_type: { natural: "Persona natural", juridica: "Persona jurídica" },
  ruc_status: {
    ACTIVO: "Activo",
    BAJA_PROVISIONAL: "Baja provisional",
    BAJA_DEFINITIVA: "Baja definitiva",
    BAJA_DE_OFICIO: "Baja de oficio",
    SUSPENSION_TEMPORAL: "Suspensión temporal",
  },
  fiscal_address_condition: { HABIDO: "Habido", NO_HABIDO: "No habido", NO_HALLADO: "No hallado", PENDIENTE: "Pendiente" },
};

function formatDate(iso: string): string {
  const [year, month, day] = iso.slice(0, 10).split("-");
  return year && month && day ? `${day}/${month}/${year}` : iso;
}

export function formatDiffValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "activities_start_date" && typeof value === "string") return formatDate(value);
  if (field in ENUM_LABELS && typeof value === "string") return ENUM_LABELS[field][value] ?? value;
  if (field === "secondary_economic_activities" && Array.isArray(value)) {
    const lines = value.map((item) => {
      if (typeof item === "string") return item; // formato del cliente: "CIIU - descripcion"
      const activity = item as { ciiu?: string | null; descripcion?: string; description?: string };
      const text = activity.descripcion ?? activity.description ?? "";
      return activity.ciiu ? `${activity.ciiu} - ${text}` : text;
    });
    return lines.length ? lines.join("; ") : "—";
  }
  if (field === "tributos_afectos" && Array.isArray(value)) {
    const lines = value.map((row) => {
      const r = row as { tributo?: string; afecto_desde?: string | null };
      return r.afecto_desde ? `${r.tributo} (desde ${formatDate(r.afecto_desde)})` : String(r.tributo ?? "");
    });
    return lines.length ? lines.join("; ") : "—";
  }
  if (field === "establecimientos_anexos" && Array.isArray(value)) {
    const lines = value.map((row) => {
      const r = row as { codigo?: string; denominacion?: string | null; tipo?: string | null };
      return [r.codigo, r.tipo, r.denominacion].filter(Boolean).join(" · ");
    });
    return lines.length ? lines.join("; ") : "—";
  }
  if (field === "representantes_legales" && Array.isArray(value)) {
    const lines = value.map((row) => {
      const r = row as { nombre?: string; cargo?: string | null };
      return [r.nombre, r.cargo].filter(Boolean).join(" · ");
    });
    return lines.length ? lines.join("; ") : "—";
  }
  return typeof value === "string" ? value : JSON.stringify(value);
}

export interface AcceptSelection {
  accepted: string[];
  overrides: Record<string, string>;
}

// Solo campos con cambio; las correcciones solo para campos permitidos, aceptados y realmente distintos de la propuesta.
export function buildSelection(diff: DiffEntry[], selected: Iterable<string>, edits: Record<string, string>): AcceptSelection {
  const allowed = new Set(allChangedFields(diff));
  const accepted = [...new Set(selected)].filter((field) => allowed.has(field));
  const overrides: Record<string, string> = {};
  for (const field of accepted) {
    const edited = edits[field]?.trim();
    const entry = diff.find((d) => d.field === field);
    if (edited && canOverrideField(field) && entry && edited !== String(entry.proposed ?? "")) overrides[field] = edited;
  }
  return { accepted, overrides };
}

export function isPendingReview(snapshot: Pick<FichaSnapshot, "review_status">): boolean {
  return snapshot.review_status === "PENDING_REVIEW";
}

const REVIEW_STATUS_LABEL: Record<string, string> = {
  PENDING_REVIEW: "Pendiente de revisión",
  ACCEPTED: "Aceptada",
  REJECTED: "Rechazada",
  SUPERSEDED: "Reemplazada por una propuesta más reciente",
};

export function describeReviewStatus(status: string): string {
  return REVIEW_STATUS_LABEL[status] ?? "Estado desconocido";
}

const FICHA_ERRORS: Record<string, string> = {
  FICHA_SNAPSHOT_NOT_FOUND: "La propuesta ya no existe.",
  FICHA_CLIENT_NOT_FOUND: "El cliente no existe.",
  FICHA_SNAPSHOT_VERSION_CONFLICT: "La propuesta cambió mientras la revisabas. Recarga e inténtalo otra vez.",
  FICHA_SNAPSHOT_NOT_PENDING: "Esta propuesta ya fue revisada o reemplazada.",
  FICHA_INVALID_FIELD_SELECTION: "Selecciona solo campos que tengan cambios.",
  FICHA_INVALID_OVERRIDE: "Esa corrección no está permitida.",
  ONBOARDING_NOT_FOUND: "El alta ya no existe.",
  ONBOARDING_VERSION_CONFLICT: "El alta cambió mientras la revisabas. Recarga e inténtalo otra vez.",
  ONBOARDING_NOT_AWAITING_REVIEW: "El alta no está esperando tu revisión.",
  ONBOARDING_ALREADY_COMPLETED: "El alta ya fue finalizada.",
  ONBOARDING_CLIENT_ALREADY_EXISTS: "Ya existe un contribuyente con ese RUC.",
  ONBOARDING_RUC_INVALID: "El RUC no es válido.",
  ONBOARDING_RUC_PREFIX_NOT_SUPPORTED: "El alta asistida solo aplica a RUC que empiezan con 10 o 20. Usa el alta manual.",
  PERMISSION_DENIED: "No tienes permiso para esta operación.",
};
const GENERIC = "No se pudo completar la operación. Inténtalo otra vez.";

export function describeFichaError(detail: unknown): string {
  let code: string | null = null;
  if (typeof detail === "string") code = detail;
  else if (detail && typeof detail === "object" && "code" in detail) {
    const c = (detail as { code: unknown }).code;
    code = typeof c === "string" ? c : null;
  }
  return (code && FICHA_ERRORS[code]) || GENERIC;
}

// --- validacion de RUC para UX (el backend vuelve a validar) ---

const CHECK_FACTORS = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];

export function isValidRuc(ruc: string): boolean {
  if (!/^\d{11}$/.test(ruc)) return false;
  if (!["10", "15", "16", "17", "20"].includes(ruc.slice(0, 2))) return false;
  const total = CHECK_FACTORS.reduce((sum, factor, index) => sum + Number(ruc[index]) * factor, 0);
  const result = 11 - (total % 11);
  const expected = result === 10 ? 0 : result === 11 ? 1 : result;
  return expected === Number(ruc[10]);
}

export type RucCheck = "ok" | "invalid" | "unsupported_prefix";

// El alta asistida solo deduce el tipo de persona de los prefijos 10 y 20.
export function checkOnboardingRuc(ruc: string): RucCheck {
  if (!isValidRuc(ruc)) return "invalid";
  return ruc.startsWith("10") || ruc.startsWith("20") ? "ok" : "unsupported_prefix";
}

export const RUC_CHECK_MESSAGE: Record<RucCheck, string | null> = {
  ok: null,
  invalid: "El RUC no es válido (11 dígitos y dígito verificador).",
  unsupported_prefix: "El alta asistida solo aplica a RUC que empiezan con 10 o 20. Usa el alta manual.",
};

// --- pasos del alta asistida (derivados del ESTADO AUTORITATIVO; nunca de memoria del navegador) ---

export type OnboardingStep = "credentials" | "waiting" | "failed" | "review" | "done" | "cancelled";

const ACTIVE_SESSION_STATES = new Set(["CREATED", "WAITING_FOR_AGENT", "IN_PROGRESS", "WAITING_FOR_USER", "HUMAN_CONTROL"]);

export function deriveOnboardingStep(
  onboarding: Pick<Onboarding, "status"> | null,
  session: Pick<SolAccessSessionOut, "status"> | null,
): OnboardingStep {
  if (!onboarding) return "credentials";
  if (onboarding.status === "COMPLETED") return "done";
  if (onboarding.status === "CANCELLED") return "cancelled";
  if (onboarding.status === "AWAITING_REVIEW") return "review";
  // IN_PROGRESS / FAILED: depende de la sesion de extraccion. Con el cliente provisional ya creado, "sin sesion activa"
  // significa que hay que REINTENTAR (la credencial ya esta en la boveda: no se vuelven a pedir secretos).
  if (session && ACTIVE_SESSION_STATES.has(session.status)) return "waiting";
  return "failed";
}

// El alta necesita REINTENTO (sin volver a pedir secretos) si hay cliente provisional pero la sesion termino sin propuesta.
export function canRetryWithoutSecrets(step: OnboardingStep): boolean {
  return step === "failed";
}

// Campos del formulario que JAMAS se persisten (ni en storage ni en URL): se usan para pruebas estaticas.
export const SECRET_FORM_FIELDS = ["sol_username", "sol_password", "vault_password"] as const;

export function onboardingResumeQuery(onboardingId: string): string {
  return `?onboarding=${encodeURIComponent(onboardingId)}`;
}


// --- «Información SUNAT» y Flujo B (actualizar un cliente existente) ---

export interface RegimeView {
  confirmed: boolean;
  text: string; // lo que se muestra: el regimen OFICIAL o la aclaracion de que NO esta confirmado
  asOf: string | null;
}

// El regimen oficial sale SOLO del registro de tributos afectos del ultimo snapshot aceptado; jamas del valor interno por defecto.
export function describeOfficialRegime(overview: Pick<FichaOverview, "official_regime" | "internal_tax_regime"> | null): RegimeView {
  if (!overview || overview.official_regime.value === null) {
    return {
      confirmed: false,
      text: "Régimen no confirmado por SUNAT. Actualiza la Ficha RUC desde el Centro SUNAT para obtenerlo.",
      asOf: null,
    };
  }
  return {
    confirmed: true,
    text: TAX_REGIME_LABEL[overview.official_regime.value] ?? overview.official_regime.label ?? overview.official_regime.value,
    asOf: overview.official_regime.as_of,
  };
}

// El valor interno del cliente se rotula como interno (nunca como dato oficial) y avisa si difiere del oficial.
export function describeInternalRegime(
  overview: Pick<FichaOverview, "official_regime" | "internal_tax_regime"> & Partial<Pick<FichaOverview, "internal_regime_source" | "internal_regime_changed_at">>,
): string {
  const internal = TAX_REGIME_LABEL[overview.internal_tax_regime] ?? overview.internal_tax_regime;
  const official = overview.official_regime.value;
  if (official && official !== overview.internal_tax_regime) {
    return `${internal} (dato interno; difiere del oficial: revisa la propuesta de la Ficha)`;
  }
  if (overview.internal_regime_source === "MANUAL") {
    const when = overview.internal_regime_changed_at ? ` el ${new Date(overview.internal_regime_changed_at).toLocaleString("es-PE")}` : "";
    return official ? `${internal} (corregido manualmente${when}; coincide con SUNAT)` : `${internal} (corregido manualmente${when}; no extraído de SUNAT)`;
  }
  if (overview.internal_regime_source === "SUNAT_FICHA_RUC") return `${internal} (aceptado desde la Ficha RUC de SUNAT)`;
  return official ? `${internal} (coincide con SUNAT)` : `${internal} (dato interno, sin confirmar con SUNAT)`;
}

export type VerificationOutcome = "UNCHANGED" | "PROPOSAL" | "NONE";

// Resultado de la ULTIMA actualizacion: «sin cambios» solo si la verificacion sin cambios ocurrio DESPUES de iniciar la sesion;
// «propuesta» si hay una pendiente. Nunca se fabrica una diferencia para forzar una confirmacion.
export function verificationOutcome(
  overview: Pick<FichaOverview, "pending_snapshot_id" | "last_unchanged_verification_at"> | null,
  sessionCreatedAt: string | null,
): VerificationOutcome {
  if (!overview) return "NONE";
  if (overview.pending_snapshot_id) return "PROPOSAL";
  if (overview.last_unchanged_verification_at && sessionCreatedAt && new Date(overview.last_unchanged_verification_at) >= new Date(sessionCreatedAt)) {
    return "UNCHANGED";
  }
  return "NONE";
}

export const UNCHANGED_MESSAGE = "Ficha RUC verificada, sin cambios";

export function formatDateTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("es-PE") : "—";
}
