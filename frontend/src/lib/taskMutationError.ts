import { ApiError } from "@/lib/apiClient";

export type TaskMutationErrorKind = "VERSION_CONFLICT" | "NO_CHANGES" | "OTHER";

export interface TaskMutationErrorResult {
  kind: TaskMutationErrorKind;
  message: string;
}

/**
 * Clasifica el error de una mutación de tarea (PATCH/start/complete/cancel/
 * reassign/reschedule/comments). Solo un 409 con `detail.code` reconocido se
 * trata como conflicto de versión o "sin cambios reales" -- cualquier otro
 * error (incluido un 409 con otro código) cae en "OTHER".
 */
export function classifyTaskMutationError(err: unknown): TaskMutationErrorResult {
  if (err instanceof ApiError && err.status === 409) {
    const detail = err.detail as { code?: string; message?: string } | string;
    const code = typeof detail === "object" ? detail.code : null;
    if (code === "TASK_VERSION_CONFLICT") {
      return {
        kind: "VERSION_CONFLICT",
        message: "Esta tarea fue modificada por otra persona. Se recargó la información más reciente.",
      };
    }
    if (code === "TASK_NO_CHANGES") {
      return { kind: "NO_CHANGES", message: "No hay ningún cambio real que aplicar." };
    }
  }
  return {
    kind: "OTHER",
    message: err instanceof ApiError ? String(err.detail) : "Ocurrió un error inesperado.",
  };
}
