import type { CommunicationDraftOut } from "@/lib/types/communication";

/**
 * Autorización VISUAL por fila, exclusiva del rol asistente (mismo patrón
 * que `isTaskOwnedByAsistente`, ver ADR-0061/ADR-0067): admin y contador
 * siempre pueden ver/operar cualquier borrador del tenant; el asistente
 * solo los que él mismo creó. El backend ya aplica esta misma restricción
 * en el servicio -- esta función solo evita mostrar botones de acción que
 * el servidor rechazaría de todos modos.
 */
export function isCommunicationDraftOwnedByAsistente(
  user: { role: string; id: string } | null | undefined,
  draft: Pick<CommunicationDraftOut, "created_by">,
): boolean {
  if (!user || user.role !== "asistente") return true;
  return draft.created_by === user.id;
}
