import { ApiError } from "@/lib/apiClient";

export type CommunicationMutationErrorKind = "VERSION_CONFLICT" | "INVALID_TRANSITION" | "OTHER";

export interface CommunicationMutationErrorResult {
  kind: CommunicationMutationErrorKind;
  message: string;
}

const KNOWN_MESSAGES: Record<string, string> = {
  DRAFT_NOT_FOUND: "El borrador ya no existe.",
  DRAFT_NOT_OWNED: "Este borrador no te pertenece.",
  SOURCE_NOT_FOUND: "El cliente, tarea o documento seleccionado no existe.",
  SOURCE_CLIENT_MISMATCH: "La tarea o documento seleccionado no pertenece a este cliente.",
  SOURCE_NOT_ACCESSIBLE: "No tienes acceso a la tarea u origen seleccionado.",
  WHATSAPP_NUMBER_REQUIRED: "El cliente no tiene un número de WhatsApp registrado.",
  INVALID_WHATSAPP_NUMBER: "El número de WhatsApp no es válido. Usa formato internacional (+51...).",
  TEMPLATE_NOT_FOUND: "La plantilla seleccionada no existe.",
  UNKNOWN_TEMPLATE_VARIABLE: "La plantilla no admite alguna de las variables enviadas.",
};

/**
 * Clasifica el error de una mutación de comunicación (crear borrador,
 * previsualizar plantilla, mark-open-requested/mark-sent/discard). Un 409
 * VERSION_CONFLICT o INVALID_COMMUNICATION_TRANSITION indica que el estado
 * cambió bajo nuestros pies -- ambos casos deben recargar el borrador desde
 * el servidor, nunca reintentar a ciegas con la misma versión.
 */
export function classifyCommunicationMutationError(err: unknown): CommunicationMutationErrorResult {
  if (err instanceof ApiError) {
    const detail = err.detail as { code?: string; message?: string } | string;
    const code = typeof detail === "object" ? detail.code : null;

    if (err.status === 409 && code === "VERSION_CONFLICT") {
      return {
        kind: "VERSION_CONFLICT",
        message: "Este borrador fue modificado por otra persona. Se recargó la información más reciente.",
      };
    }
    if (err.status === 409 && code === "INVALID_COMMUNICATION_TRANSITION") {
      return {
        kind: "INVALID_TRANSITION",
        message: "Esta acción ya no es válida para el estado actual del borrador. Se recargó la información.",
      };
    }
    if (code && KNOWN_MESSAGES[code]) {
      return { kind: "OTHER", message: KNOWN_MESSAGES[code] };
    }
    return { kind: "OTHER", message: typeof detail === "string" ? detail : String(err.detail) };
  }
  return { kind: "OTHER", message: "Ocurrió un error inesperado." };
}
