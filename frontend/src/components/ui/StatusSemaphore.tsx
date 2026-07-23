export type SemaphoreColor = "verde" | "amarillo" | "naranja" | "rojo" | "gris";

export interface SemaphoreEntry {
  label: string;
  color: SemaphoreColor;
}

export const SERVICE_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  prospecto: { label: "Prospecto", color: "gris" },
  activo: { label: "Activo", color: "verde" },
  suspendido: { label: "Suspendido", color: "naranja" },
  pendiente_cierre: { label: "Pendiente de cierre", color: "amarillo" },
  inactivo: { label: "Inactivo", color: "rojo" },
};

export const RUC_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  ACTIVO: { label: "Activo", color: "verde" },
  BAJA_PROVISIONAL: { label: "Baja provisional", color: "amarillo" },
  BAJA_DEFINITIVA: { label: "Baja definitiva", color: "rojo" },
  BAJA_DE_OFICIO: { label: "Baja de oficio", color: "rojo" },
  SUSPENSION_TEMPORAL: { label: "Suspensión temporal", color: "naranja" },
};

export const WORKFLOW_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  PENDING: { label: "Pendiente", color: "gris" },
  DOCUMENTS_REQUESTED: { label: "Documentos solicitados", color: "amarillo" },
  INFORMATION_INCOMPLETE: { label: "Información incompleta", color: "naranja" },
  DOCUMENTS_COMPLETE: { label: "Documentos completos", color: "verde" },
  IN_PROGRESS: { label: "En proceso", color: "amarillo" },
  UNDER_REVIEW: { label: "En revisión", color: "amarillo" },
  READY_TO_FILE: { label: "Lista para presentar", color: "verde" },
  CLOSED: { label: "Cerrada", color: "verde" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
  CANCELLED: { label: "Cancelada", color: "rojo" },
};

export const FILING_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_FILED: { label: "No presentada", color: "gris" },
  READY: { label: "Lista", color: "amarillo" },
  FILED: { label: "Presentada", color: "verde" },
  FAILED: { label: "Fallida", color: "rojo" },
  NOT_REQUIRED: { label: "No requerida", color: "gris" },
};

export const RISK_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  CANCELLED: { label: "Cancelada", color: "gris" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
  CLOSED: { label: "Cerrada", color: "verde" },
  CLOSED_WITH_PENDING: { label: "Cerrada con pendientes", color: "naranja" },
  ON_TRACK: { label: "En curso", color: "verde" },
  PENDING_TAX: { label: "Impuesto pendiente", color: "naranja" },
  PENDING_DOCUMENTS: { label: "Documentos pendientes", color: "naranja" },
  UPCOMING: { label: "Próxima", color: "amarillo" },
  DUE_TODAY: { label: "Vence hoy", color: "naranja" },
  OVERDUE: { label: "Vencida", color: "rojo" },
  CRITICAL: { label: "Crítica", color: "rojo" },
};

export const TAX_PAYMENT_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_DETERMINED: { label: "Sin determinar", color: "gris" },
  NO_TAX_DUE: { label: "Sin impuesto", color: "verde" },
  CREDIT_BALANCE: { label: "Saldo a favor", color: "verde" },
  PAYMENT_PENDING: { label: "Pago pendiente", color: "amarillo" },
  PARTIALLY_PAID: { label: "Pago parcial", color: "naranja" },
  PAID: { label: "Pagado", color: "verde" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
};

export const DOCUMENT_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_CONFIGURED: { label: "Sin configurar", color: "gris" },
  PENDING: { label: "Pendiente", color: "amarillo" },
  PARTIAL: { label: "Parcial", color: "naranja" },
  OBSERVED: { label: "Observado", color: "rojo" },
  COMPLETE: { label: "Completo", color: "verde" },
  NOT_APPLICABLE: { label: "No aplica", color: "gris" },
};

export const FEE_AGREEMENT_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  ACTIVE: { label: "Activo", color: "verde" },
  SUPERSEDED: { label: "Reemplazado", color: "gris" },
  CANCELLED: { label: "Cancelado", color: "rojo" },
};

export const FEE_CHARGE_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  PENDING_ISSUANCE: { label: "Sin emitir", color: "gris" },
  ISSUED: { label: "Emitido", color: "amarillo" },
  PAYMENT_PENDING: { label: "Pago pendiente", color: "amarillo" },
  PARTIALLY_PAID: { label: "Pago parcial", color: "naranja" },
  PAID: { label: "Pagado", color: "verde" },
  CANCELLED: { label: "Cancelado", color: "rojo" },
};

export const FEE_COLLECTION_RISK_SEMAPHORE: Record<string, SemaphoreEntry> = {
  ON_TRACK: { label: "En curso", color: "verde" },
  DUE_TODAY: { label: "Vence hoy", color: "naranja" },
  OVERDUE: { label: "Vencido", color: "rojo" },
  PAID: { label: "Pagado", color: "verde" },
  CANCELLED: { label: "Cancelado", color: "gris" },
};

export const TAX_PERIOD_TYPE_LABEL: Record<string, string> = {
  MONTHLY: "Mensual",
  ANNUAL: "Anual",
  SPECIAL: "Especial",
};

export const GENERATION_RUN_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  PENDING: { label: "Pendiente", color: "gris" },
  PREVIEWED: { label: "Previsualizado", color: "amarillo" },
  STALE: { label: "Desactualizado", color: "naranja" },
  CANCELLED: { label: "Cancelado", color: "rojo" },
  COMPLETED: { label: "Completado", color: "verde" },
  COMPLETED_WITH_ERRORS: { label: "Completado con errores", color: "naranja" },
  FAILED: { label: "Fallido", color: "rojo" },
};

interface StatusSemaphoreProps {
  status: string;
  map: Record<string, SemaphoreEntry>;
}

export function StatusSemaphore({ status, map }: StatusSemaphoreProps) {
  const entry = map[status] ?? { label: status, color: "gris" as const };
  return <span className={`status-pill status-${entry.color}`}>{entry.label}</span>;
}
