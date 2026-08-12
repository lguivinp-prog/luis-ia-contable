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

export const TASK_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  PENDING: { label: "Pendiente", color: "gris" },
  IN_PROGRESS: { label: "En proceso", color: "amarillo" },
  COMPLETED: { label: "Completada", color: "verde" },
  CANCELLED: { label: "Cancelada", color: "rojo" },
};

export const TASK_PRIORITY_SEMAPHORE: Record<string, SemaphoreEntry> = {
  LOW: { label: "Baja", color: "gris" },
  NORMAL: { label: "Normal", color: "verde" },
  HIGH: { label: "Alta", color: "amarillo" },
  URGENT: { label: "Urgente", color: "rojo" },
};

export const COMMUNICATION_DRAFT_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  DRAFT: { label: "Borrador", color: "gris" },
  OPEN_REQUESTED: { label: "Se solicitó abrir WhatsApp", color: "amarillo" },
  MARKED_SENT: { label: "Marcado como enviado por ti", color: "verde" },
  DISCARDED: { label: "Descartado", color: "rojo" },
};

export const SOL_VAULT_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NOT_INITIALIZED: { label: "No inicializada", color: "gris" },
  ACTIVE: { label: "Activa", color: "verde" },
  RECOVERY_REQUIRED: { label: "Requiere recuperación", color: "rojo" },
  DISABLED: { label: "Deshabilitada", color: "rojo" },
};

export const SOL_CREDENTIAL_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  NEEDS_VALIDATION: { label: "Pendiente de validación", color: "amarillo" },
  ACTIVE: { label: "Activa", color: "verde" },
  INVALID: { label: "Inválida", color: "rojo" },
  DISABLED: { label: "Deshabilitada", color: "gris" },
};

export const SOL_VAULT_SLOT_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  ACTIVE: { label: "Activo", color: "verde" },
  REVOKED: { label: "Revocado", color: "gris" },
};

export const SOL_VAULT_INVITATION_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  PENDING: { label: "Pendiente", color: "amarillo" },
  ACCEPTED: { label: "Aceptada", color: "verde" },
  EXPIRED: { label: "Vencida", color: "rojo" },
  CANCELLED: { label: "Cancelada", color: "gris" },
};

export const TASK_TYPE_LABEL: Record<string, string> = {
  DECLARATION_FILING: "Presentación de declaración",
  DOCUMENT_COLLECTION: "Recopilación de documento",
  TAX_PAYMENT_FOLLOWUP: "Seguimiento de pago tributario",
  FEE_COLLECTION: "Cobranza de honorarios",
  CLIENT_FOLLOWUP: "Seguimiento de cliente",
  INTERNAL_REVIEW: "Revisión interna",
  GENERAL: "General",
};

interface StatusSemaphoreProps {
  status: string;
  map: Record<string, SemaphoreEntry>;
}

export function StatusSemaphore({ status, map }: StatusSemaphoreProps) {
  const entry = map[status] ?? { label: status, color: "gris" as const };
  return <span className={`status-pill status-${entry.color}`}>{entry.label}</span>;
}
