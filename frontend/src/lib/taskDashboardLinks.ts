import type { TaskDashboardSummary } from "@/lib/types/task";

export interface TaskSummaryCard {
  label: string;
  value: number;
  href: string;
}

/**
 * Enlaces canónicos del dashboard hacia /tasks (ver Sección 12 del plan): el
 * frontend nunca recalcula baldes localmente, siempre enlaza al filtro ya
 * resuelto por el backend (`time_bucket`/`assigned_to`/`priority`).
 */
export function buildTaskSummaryCards(summary: TaskDashboardSummary): TaskSummaryCard[] {
  return [
    { label: "Vencidas", value: summary.overdue_tasks, href: "/tasks?time_bucket=OVERDUE" },
    { label: "Vencen hoy", value: summary.due_today, href: "/tasks?time_bucket=TODAY" },
    { label: "Próximos 7 días", value: summary.due_next_7_days, href: "/tasks?time_bucket=NEXT_7_DAYS" },
    { label: "Urgentes", value: summary.urgent_tasks, href: "/tasks?priority=URGENT" },
    { label: "Sin responsable", value: summary.unassigned_tasks, href: "/tasks?assigned_to=unassigned" },
    { label: "Asignadas a mí", value: summary.assigned_to_me, href: "/tasks?assigned_to=me" },
  ];
}
