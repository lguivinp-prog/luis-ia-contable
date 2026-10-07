// Presentacion PURA del cockpit mensual (M1, Domain B). Sin red ni JSX. La logica de negocio (fase, SIRE, riesgo,
// agregacion) vive en el backend; aqui solo se traduce a etiquetas, colores y enlaces.
import type { SemaphoreEntry } from "@/components/ui/StatusSemaphore";
import type { CockpitQuery, CockpitRow, CockpitSummary } from "@/lib/types/cockpit";

export const SIRE_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_CONFIGURED: { label: "Sin configurar", color: "gris" },
  NOT_STARTED: { label: "Sin iniciar", color: "gris" },
  IN_PROGRESS: { label: "En proceso", color: "amarillo" },
  READY_FOR_SUNAT: { label: "Lista para SUNAT", color: "amarillo" },
  PRESENTED: { label: "Presentado", color: "verde" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
};

export const MONTHLY_PHASE_SEMAPHORE: Record<string, SemaphoreEntry> = {
  PRE_APERTURA: { label: "Previa", color: "gris" },
  APERTURA: { label: "Apertura", color: "amarillo" },
  OPERACION: { label: "Operación", color: "amarillo" },
  CIERRE: { label: "Cierre", color: "naranja" },
};

export const COCKPIT_RISK_SEMAPHORE: Record<string, SemaphoreEntry> = {
  CRITICAL: { label: "Crítico", color: "rojo" },
  OVERDUE: { label: "Vencido", color: "rojo" },
  DUE_TODAY: { label: "Vence hoy", color: "naranja" },
  PENDING_TAX: { label: "Impuesto pendiente", color: "naranja" },
  PENDING_DOCUMENTS: { label: "Documentos pendientes", color: "naranja" },
  PERIOD_NOT_GENERATED: { label: "Periodo sin generar", color: "naranja" },
  CLOSED_WITH_PENDING: { label: "Cerrado con pendientes", color: "naranja" },
  PENDING_FEES: { label: "Cobranza pendiente", color: "amarillo" },
  UPCOMING: { label: "Próximo", color: "amarillo" },
  ON_TRACK: { label: "En curso", color: "verde" },
  CLOSED: { label: "Cerrado", color: "verde" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
};

export const FILING_PROGRESS_LABEL: Record<string, string> = {
  NOT_GENERATED: "Periodo sin generar",
  NOT_APPLICABLE: "No aplica",
  NONE_FILED: "Ninguna presentada",
  PARTIAL: "Parcial",
  ALL_FILED: "Todas presentadas",
};

export const GENERATE_DECLARATIONS_HREF = "/declarations/generate";

export const RISK_FILTER_OPTIONS = Object.entries(COCKPIT_RISK_SEMAPHORE).map(([value, entry]) => ({
  value,
  label: entry.label,
}));
export const SIRE_FILTER_OPTIONS = Object.entries(SIRE_STATUS_SEMAPHORE).map(([value, entry]) => ({
  value,
  label: entry.label,
}));
export const FILING_PROGRESS_FILTER_OPTIONS = Object.entries(FILING_PROGRESS_LABEL).map(([value, label]) => ({
  value,
  label,
}));
export const DOCUMENT_FILTER_OPTIONS = [
  { value: "NOT_CONFIGURED", label: "Sin configurar" },
  { value: "PENDING", label: "Pendiente" },
  { value: "PARTIAL", label: "Parcial" },
  { value: "OBSERVED", label: "Observado" },
  { value: "COMPLETE", label: "Completo" },
  { value: "NOT_APPLICABLE", label: "No aplica" },
];

export function buildCockpitQueryString(query: CockpitQuery): string {
  const params = new URLSearchParams({ tax_period_id: query.tax_period_id });
  for (const key of ["search", "overall_risk", "sire_status", "document_status", "filing_progress"] as const) {
    const value = query[key]?.trim();
    if (value) params.set(key, value);
  }
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.offset !== undefined) params.set("offset", String(query.offset));
  return params.toString();
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}/${month}/${year}`;
}

export function formatPen(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return "—";
  return `S/ ${number.toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function declarationsLabel(row: CockpitRow): string {
  if (!row.period_generated) return "Periodo sin generar";
  if (row.declaration_count === 0) return "Sin obligaciones aplicables";
  return `${row.filed_declaration_count}/${row.declaration_count} presentadas`;
}

export function taxLabel(row: CockpitRow): string | null {
  if (row.tax_total_payable === null) return null;
  return `${formatPen(row.tax_total_paid)} de ${formatPen(row.tax_total_payable)}`;
}

export function feeLabel(row: CockpitRow): string | null {
  if (Number(row.fee_total_amount) === 0) return null;
  return `Saldo ${formatPen(row.fee_balance)} (de ${formatPen(row.fee_total_amount)})`;
}

export function isPeriodNotGenerated(row: CockpitRow): boolean {
  return !row.period_generated;
}

// Estados MANUALES (Buzon / Contabilidad / Comunicacion): el backend los guarda solo al primer cambio.
export const BUZON_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_REVIEWED: { label: "Sin revisar", color: "gris" },
  REVIEWED_CLEAR: { label: "Revisado sin novedades", color: "verde" },
  REVIEWED_ACTION_REQUIRED: { label: "Revisado, requiere acción", color: "naranja" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
};

export const ACCOUNTING_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_STARTED: { label: "Sin iniciar", color: "gris" },
  IN_PROGRESS: { label: "En proceso", color: "amarillo" },
  REVIEWED: { label: "Revisada", color: "verde" },
  COMPLETED: { label: "Completada", color: "verde" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
};

export const COMMUNICATION_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_STARTED: { label: "Sin iniciar", color: "gris" },
  PREPARED: { label: "Preparada", color: "amarillo" },
  SENT: { label: "Enviada", color: "verde" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
};

export interface ManualStatusOption {
  value: string;
  label: string;
}

export function semaphoreOptions(map: Record<string, SemaphoreEntry>): ManualStatusOption[] {
  return Object.entries(map).map(([value, entry]) => ({ value, label: entry.label }));
}

export interface RowAction {
  label: string;
  href: string;
}

export function workspaceHref(clientId: string, periodId: string, tab?: string): string {
  const params = new URLSearchParams({ period: periodId });
  if (tab) params.set("tab", tab);
  return `/cockpit/${clientId}?${params.toString()}`;
}

// Solo acciones REALES que llevan a paginas existentes (sin botones SUNAT todavia).
export function rowActions(row: CockpitRow): RowAction[] {
  const actions: RowAction[] = [
    { label: "Abrir workspace", href: workspaceHref(row.client_id, row.tax_period_id) },
    { label: "Ver cliente", href: `/clients/${row.client_id}` },
  ];
  if (isPeriodNotGenerated(row)) actions.push({ label: "Generar declaraciones", href: GENERATE_DECLARATIONS_HREF });
  return actions;
}

export interface SummaryCard {
  key: keyof CockpitSummary;
  label: string;
  value: number;
}

const CARD_LABELS: [keyof CockpitSummary, string][] = [
  ["active_clients", "Clientes activos"],
  ["period_not_generated", "Periodo sin generar"],
  ["due_soon", "Vencen pronto"],
  ["overdue", "Vencidos"],
  ["documents_incomplete", "Documentos incompletos"],
  ["sire_pending", "SIRE pendiente"],
  ["declarations_pending", "Declaraciones pendientes"],
  ["tax_payment_pending", "Pago de impuesto pendiente"],
  ["critical", "Críticos"],
  ["buzon_pending", "Buzón pendiente"],
  ["accounting_pending", "Contabilidad pendiente"],
  ["client_communication_pending", "Comunicación pendiente"],
];

export function buildSummaryCards(summary: CockpitSummary): SummaryCard[] {
  return CARD_LABELS.map(([key, label]) => ({ key, label, value: summary[key] }));
}

interface PeriodLike {
  id: string;
  period_type: string;
  start_date: string;
  end_date: string;
}

// Periodos mensuales, del mas reciente al mas antiguo.
export function monthlyPeriodsNewestFirst<T extends PeriodLike>(periods: T[]): T[] {
  return periods.filter((p) => p.period_type === "MONTHLY").sort((a, b) => b.end_date.localeCompare(a.end_date));
}

// Periodo por defecto: el mas reciente que ya termino antes del mes calendario en curso (el trabajo operativo de un
// periodo ocurre en el mes siguiente); si no hay, el mas reciente. `now` es inyectable para pruebas.
export function defaultPeriodId<T extends PeriodLike>(periods: T[], now: Date = new Date()): string | null {
  const monthly = monthlyPeriodsNewestFirst(periods);
  if (monthly.length === 0) return null;
  const monthStart = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
  const finished = monthly.find((p) => p.end_date < monthStart);
  return (finished ?? monthly[0]).id;
}
