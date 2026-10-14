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
  SOL_CREDENTIAL_PROFILE_NAME_TAKEN: "Ya existe un perfil activo con ese nombre para este cliente. Usa otro nombre.",
  SOL_PRIMARY_CREDENTIAL_ALREADY_EXISTS: "Este cliente ya tiene una credencial principal activa. Deja de usarla antes de registrar otra principal.",
  SOL_CREDENTIAL_SELECTION_REQUIRED: "Este cliente tiene varios perfiles: elige cuál modificar.",
  SOL_CREDENTIAL_AUTHORIZATION_EXPIRED: "La autorización del cliente ya venció. Renuévala antes de continuar.",
  SOL_CREDENTIAL_FIELD_TOO_LONG: "Uno de los textos es demasiado largo. El motivo de autorización admite hasta 60 caracteres; usa la descripción del alcance para el detalle.",
  SOL_CREDENTIAL_STORE_FAILED:
    "No se pudo guardar la credencial por un problema interno. No se modificó ningún perfil existente: verifica la lista y vuelve a intentarlo una sola vez.",
  CLIENT_NOT_FOUND: "El cliente no existe.",
};

/** Limites reales del backend (columnas de `client_sol_credentials`); el formulario los aplica ANTES de enviar. */
export const CREDENTIAL_FIELD_LIMITS = {
  profile_name: 60,
  authorization_basis: 60,
  authorization_reference: 255,
  authorized_scope_description: 2000,
} as const;

const FIELD_LABELS: Record<string, string> = {
  profile_name: "El nombre interno del perfil",
  authorization_basis: "El motivo de autorización",
  authorization_reference: "La referencia de la autorización",
  authorized_scope_description: "La descripción del alcance",
  sol_username: "El usuario SOL",
  sol_password: "La contraseña SOL",
};

export interface CredentialFormValues {
  profileName: string;
  authorizationBasis: string;
  authorizationReference: string;
  authorizedScopeDescription: string;
}

/** Validacion previa al envio (mismos limites que el servidor). Devuelve un mensaje claro o null. No inspecciona usuario ni clave. */
export function validateCredentialForm(values: CredentialFormValues): string | null {
  const checks: [string, string][] = [
    ["profile_name", values.profileName.trim()],
    ["authorization_basis", values.authorizationBasis],
    ["authorization_reference", values.authorizationReference],
    ["authorized_scope_description", values.authorizedScopeDescription],
  ];
  for (const [field, value] of checks) {
    const limit = CREDENTIAL_FIELD_LIMITS[field as keyof typeof CREDENTIAL_FIELD_LIMITS];
    if (value.length > limit) {
      const hint = field === "authorization_basis" ? " Usa el campo «Descripción del alcance» para el detalle." : "";
      return `${FIELD_LABELS[field]} admite hasta ${limit} caracteres (tiene ${value.length}).${hint}`;
    }
  }
  return null;
}

/** Interpreta el 422 de validacion de FastAPI (lista de errores) SIN reproducir el valor enviado. */
function describeValidationDetail(detail: unknown): string | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const first = detail[0] as { loc?: unknown[]; type?: string; ctx?: { max_length?: number } } | undefined;
  const field = Array.isArray(first?.loc) ? String(first.loc[first.loc.length - 1]) : "";
  const label = FIELD_LABELS[field];
  if (!label) return "Revisa los datos del formulario: alguno no tiene un formato válido.";
  if (first?.type === "string_too_long") return `${label} es demasiado largo${first.ctx?.max_length ? ` (máximo ${first.ctx.max_length} caracteres)` : ""}.`;
  return `${label} no es válido.`;
}

/**
 * Tras un fallo ambiguo (500, red, tiempo agotado) la operacion pudo haberse confirmado. Si aparece un perfil NUEVO del mismo tipo (y nombre, cuando se
 * indico) que no estaba antes, se considera guardado: no se vuelve a enviar nada.
 */
export function findPersistedProfile<T extends { id: string; credential_kind: string; profile_name?: string | null }>(
  before: T[],
  after: T[],
  kind: string,
  profileName: string,
): T | null {
  const known = new Set(before.map((p) => p.id));
  const wanted = profileName.trim().toLowerCase();
  return after.find((p) => !known.has(p.id) && p.credential_kind === kind && (!wanted || (p.profile_name ?? "").trim().toLowerCase() === wanted)) ?? null;
}

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
  const validation = describeValidationDetail(detail);
  if (validation) return validation;
  const code = extractErrorCode(detail);
  if (!code) return _FALLBACK_MESSAGE;
  return SOL_VAULT_ERROR_MESSAGES[code] ?? _FALLBACK_MESSAGE;
}
