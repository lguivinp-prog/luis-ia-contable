import { apiClient } from "@/lib/apiClient";
import type {
  AgendaSummary,
  AlertsSummary,
  OperationalTask,
  OperationalTaskDetail,
  TaskChangeHistoryListOut,
  TaskCommentListOut,
  TaskDueInput,
  TaskListOut,
  TaskPriority,
  TaskType,
} from "@/lib/types/task";

export interface TaskFilters {
  status?: string;
  priority?: string;
  task_type?: string;
  time_bucket?: string;
  assigned_to?: string;
  client_id?: string;
  limit?: number;
  offset?: number;
}

export function buildTaskQuery(filters: TaskFilters): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const qs = query.toString();
  return qs ? `?${qs}` : "";
}

export function listTasks(filters: TaskFilters): Promise<TaskListOut> {
  return apiClient.get<TaskListOut>(`/tasks${buildTaskQuery(filters)}`);
}

export function getTask(taskId: string): Promise<OperationalTaskDetail> {
  return apiClient.get<OperationalTaskDetail>(`/tasks/${taskId}`);
}

export interface TaskCreatePayload {
  title: string;
  description: string | null;
  task_type: TaskType;
  priority: TaskPriority;
  due: TaskDueInput;
  assigned_to?: string | null;
  client_id?: string | null;
}

export function createTask(payload: TaskCreatePayload): Promise<OperationalTaskDetail> {
  return apiClient.post<OperationalTaskDetail>("/tasks", payload);
}

export interface TaskUpdatePayload {
  expected_version: number;
  title?: string;
  description?: string | null;
  priority?: TaskPriority;
  task_type?: TaskType;
}

export function updateTask(taskId: string, payload: TaskUpdatePayload): Promise<OperationalTaskDetail> {
  return apiClient.patch<OperationalTaskDetail>(`/tasks/${taskId}`, payload);
}

export function startTask(taskId: string, expectedVersion: number): Promise<OperationalTaskDetail> {
  return apiClient.post<OperationalTaskDetail>(`/tasks/${taskId}/start`, {
    expected_version: expectedVersion,
  });
}

export function completeTask(taskId: string, expectedVersion: number): Promise<OperationalTaskDetail> {
  return apiClient.post<OperationalTaskDetail>(`/tasks/${taskId}/complete`, {
    expected_version: expectedVersion,
  });
}

export function cancelTask(
  taskId: string,
  expectedVersion: number,
  reason: string,
): Promise<OperationalTaskDetail> {
  return apiClient.post<OperationalTaskDetail>(`/tasks/${taskId}/cancel`, {
    expected_version: expectedVersion,
    reason,
  });
}

export function reassignTask(
  taskId: string,
  expectedVersion: number,
  assignedTo: string | null,
): Promise<OperationalTaskDetail> {
  return apiClient.post<OperationalTaskDetail>(`/tasks/${taskId}/reassign`, {
    expected_version: expectedVersion,
    assigned_to: assignedTo,
  });
}

export function rescheduleTask(
  taskId: string,
  expectedVersion: number,
  due: TaskDueInput,
): Promise<OperationalTaskDetail> {
  return apiClient.post<OperationalTaskDetail>(`/tasks/${taskId}/reschedule`, {
    expected_version: expectedVersion,
    due,
  });
}

export function listTaskComments(taskId: string): Promise<TaskCommentListOut> {
  return apiClient.get<TaskCommentListOut>(`/tasks/${taskId}/comments`);
}

export function createTaskComment(taskId: string, body: string): Promise<OperationalTask> {
  return apiClient.post(`/tasks/${taskId}/comments`, { body });
}

export function listTaskHistory(taskId: string): Promise<TaskChangeHistoryListOut> {
  return apiClient.get<TaskChangeHistoryListOut>(`/tasks/${taskId}/history`);
}

export function syncTasks(
  triggerSource: "MANUAL" | "LOGIN" | "AGENDA_OPEN" | "DASHBOARD_OPEN",
): Promise<{ run_id: string; summary: Record<string, number> }> {
  return apiClient.post("/tasks/sync", { trigger_source: triggerSource });
}

export function getAgendaSummary(): Promise<AgendaSummary> {
  return apiClient.get<AgendaSummary>("/tasks/agenda-summary");
}

export function getAlertsSummary(): Promise<AlertsSummary> {
  return apiClient.get<AlertsSummary>("/tasks/alerts-summary");
}
