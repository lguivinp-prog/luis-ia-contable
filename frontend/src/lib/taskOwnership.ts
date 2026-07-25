import type { OperationalTaskDetail } from "@/lib/types/task";

/**
 * Autorización VISUAL por fila, exclusiva del rol asistente (ver ADR-0061):
 * admin y contador siempre pueden operar cualquier tarea del tenant; el
 * asistente solo la suya. Esto nunca reconstruye la matriz rol->permiso --
 * `hasPermission` sigue siendo la única fuente de gateo por permiso; esta
 * función añade únicamente el filtro adicional por fila que el backend
 * también aplica en el servicio.
 */
export function isTaskOwnedByAsistente(
  user: { role: string; id: string } | null | undefined,
  task: Pick<OperationalTaskDetail, "assigned_to">,
): boolean {
  if (!user || user.role !== "asistente") return true;
  return task.assigned_to === user.id;
}
