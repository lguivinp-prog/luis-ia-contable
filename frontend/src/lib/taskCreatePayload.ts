import type { TaskCreatePayload } from "@/lib/tasks";
import type { TaskPriority, TaskType } from "@/lib/types/task";

export interface TaskCreateForm {
  title: string;
  description: string;
  taskType: TaskType;
  priority: TaskPriority;
  dueDate: string;
  dueTime: string;
  clientId: string;
}

/**
 * Construye el payload de POST /tasks. `client_id` se omite por completo del
 * objeto (nunca `client_id: null`) cuando el formulario tiene seleccionado
 * "Sin cliente" (`clientId === ""`) -- distinto de enviar explícitamente un
 * cliente. `TaskCreatePayload.client_id` es opcional exactamente por esto.
 */
export function buildTaskCreatePayload(form: TaskCreateForm): TaskCreatePayload {
  const payload: TaskCreatePayload = {
    title: form.title.trim(),
    description: form.description.trim() || null,
    task_type: form.taskType,
    priority: form.priority,
    due: { due_date: form.dueDate, due_time: form.dueTime || null },
  };
  if (form.clientId) {
    payload.client_id = form.clientId;
  }
  return payload;
}
