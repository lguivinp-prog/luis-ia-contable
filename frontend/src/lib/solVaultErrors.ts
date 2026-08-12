// Mapeo de codigos de error normalizados de la boveda SOL (Fase 5, Bloque
// 5.1, ver ADR-0068 a ADR-0071) a mensajes legibles. Funcion pura (sin
// JSX) para poder probarse con vitest sin entorno DOM, mismo patron que
// `googleIntegrationErrors.ts`.
export const SOL_VAULT_ERROR_MESSAGES: Record<string, string> = {
  SOL_VAULT_ALREADY_INITIALIZED: "La bóveda ya está inicializada.",
  SOL_VAULT_NOT_INITIALIZED: "La bóveda todavía no está inicializada.",
  SOL_VAULT_DISABLED: "La bóveda está deshabilitada. Contacta a un administrador.",
  SOL_VAULT_BAD_PASSWORD: "Contraseña o código de recuperación incorrecto.",
  VERSION_CONFLICT: "Alguien más modificó la bóveda mientras tanto. Recarga e inténtalo de nuevo.",
  SOL_VAULT_RECOVERY_NOT_CONFIRMED: "Primero debes confirmar el código de recuperación.",
  SOL_VAULT_LAST_USER_SLOT: "No puedes revocar el último acceso de usuario sin una recuperación confirmada.",
  SOL_VAULT_LAST_RECOVERY_SLOT: "No puedes revocar la recuperación mientras no quede ningún usuario activo.",
  SOL_VAULT_RECOVERY_REPLACEMENT_ALREADY_PENDING:
    "Ya existe un código de recuperación nuevo pendiente de confirmar.",
  SOL_VAULT_INVITATION_NOT_FOUND: "La invitación no existe.",
  SOL_VAULT_INVITATION_INVITER_NOT_UNLOCKED: "Quien invitó ya no tiene la bóveda desbloqueada.",
  SOL_VAULT_INVITATION_WRONG_USER: "Esta invitación no te corresponde.",
  SOL_VAULT_INVITATION_EXPIRED: "La invitación ya no está pendiente o venció.",
  SOL_VAULT_INVITATION_ALREADY_PENDING: "Ya hay una invitación pendiente para este usuario.",
  SOL_VAULT_INVITATION_NOT_PENDING: "La invitación ya no está pendiente.",
  SOL_VAULT_INVITATION_CANNOT_TARGET_SELF: "No puedes invitarte a ti mismo.",
  SOL_VAULT_SLOT_NOT_FOUND: "El acceso indicado no existe.",
  SOL_VAULT_LOCKED: "La bóveda está bloqueada. Desbloquéala primero.",
  SOL_VAULT_UNLOCK_EXPIRED: "El desbloqueo venció o fue revocado. Vuelve a desbloquear la bóveda.",
  SOL_VAULT_OPERATION_NOT_AUTHORIZED: "No tienes autorización para esta operación sobre la bóveda.",
  SOL_CREDENTIAL_NOT_FOUND: "No hay una credencial SOL registrada para este cliente.",
  SOL_CREDENTIAL_ALREADY_DISABLED: "Esta credencial ya está deshabilitada.",
  SOL_CREDENTIAL_AUTHORIZATION_EXPIRED: "La autorización del cliente ya venció. Renuévala antes de continuar.",
  CLIENT_NOT_FOUND: "El cliente no existe.",
};

const _FALLBACK_MESSAGE = "No se pudo completar la operación sobre la bóveda SOL.";

export function extractErrorCode(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "code" in detail) {
    const code = (detail as { code: unknown }).code;
    return typeof code === "string" ? code : null;
  }
  return null;
}

export function describeSolVaultError(detail: unknown): string {
  const code = extractErrorCode(detail);
  if (!code) return _FALLBACK_MESSAGE;
  return SOL_VAULT_ERROR_MESSAGES[code] ?? _FALLBACK_MESSAGE;
}
