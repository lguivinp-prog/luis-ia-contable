// Gestion operativa de tareas (Fase 2, Bloque 1) -- espejo exacto de
// app/schemas/task.py. El frontend nunca recalcula baldes de vencimiento
// localmente: siempre confia en los campos/filtros ya resueltos por el
// backend (ver Seccion 11/12 del plan).

export type TaskStatus = "PENDING" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED";
export type TaskPriority = "LOW" | "NORMAL" | "HIGH" | "URGENT";
export type TaskType =
  | "DECLARATION_FILING"
  | "DOCUMENT_COLLECTION"
  | "TAX_PAYMENT_FOLLOWUP"
  | "FEE_COLLECTION"
  | "CLIENT_FOLLOWUP"
  | "INTERNAL_REVIEW"
  | "GENERAL";

export const MANUAL_TASK_TYPES: { value: TaskType; label: string }[] = [
  { value: "TAX_PAYMENT_FOLLOWUP", label: "Seguimiento de pago tributario" },
  { value: "FEE_COLLECTION", label: "Cobranza de honorarios" },
  { value: "CLIENT_FOLLOWUP", label: "Seguimiento de cliente" },
  { value: "INTERNAL_REVIEW", label: "Revisión interna" },
  { value: "GENERAL", label: "General" },
];

export type TimeBucket = "OVERDUE" | "TODAY" | "NEXT_7_DAYS";

export interface TaskDueInput {
  due_date: string;
  due_time?: string | null;
  starts_date?: string | null;
  starts_time?: string | null;
}

export interface OperationalTask {
  id: string;
  tenant_id: string;
  client_id: string | null;
  declaration_id: string | null;
  declaration_document_item_id: string | null;
  professional_fee_charge_id: string | null;
  professional_fee_agreement_id: string | null;
  tax_payment_id: string | null;
  title: string;
  description: string | null;
  task_type: TaskType;
  priority: TaskPriority;
  status: TaskStatus;
  assigned_to: string | null;
  created_by: string | null;
  due_at: string;
  due_has_time: boolean;
  source_due_at: string | null;
  due_is_overridden: boolean;
  starts_at: string | null;
  completed_at: string | null;
  completed_by: string | null;
  cancelled_at: string | null;
  cancelled_by: string | null;
  cancellation_reason: string | null;
  resolution_origin: "MANUAL" | "SYSTEM" | null;
  generation_key: string | null;
  is_system_generated: boolean;
  generation_run_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface OperationalTaskDetail extends OperationalTask {
  assignee_is_active: boolean | null;
  source_due_mismatch: boolean;
}

export interface TaskListOut {
  items: OperationalTask[];
  total: number;
  limit: number;
  offset: number;
}

export interface TaskComment {
  id: string;
  task_id: string;
  author_user_id: string;
  body: string;
  created_at: string;
}

export interface TaskCommentListOut {
  items: TaskComment[];
  total: number;
  limit: number;
  offset: number;
}

export interface TaskChangeHistoryEntry {
  id: string;
  task_id: string;
  actor_user_id: string | null;
  created_at: string;
  reason: string | null;
  origin: "MANUAL" | "SYSTEM";
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  from_assigned_to: string | null;
  to_assigned_to: string | null;
  from_due_at: string | null;
  to_due_at: string | null;
  from_priority: string | null;
  to_priority: string | null;
}

export interface TaskChangeHistoryListOut {
  items: TaskChangeHistoryEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface AgendaSummary {
  overdue_tasks: number;
  due_today: number;
  due_next_7_days: number;
  urgent_tasks: number;
  unassigned_tasks: number;
}

export interface AlertsSummary {
  overdue_tasks: number;
  due_today: number;
  urgent_tasks: number;
  unassigned_tasks: number;
  source_due_mismatch: number;
}

export interface TaskDashboardSummary extends AgendaSummary {
  assigned_to_me: number;
}
