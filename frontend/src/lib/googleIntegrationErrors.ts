// Mapeo de codigos de error normalizados del callback OAuth Google (Fase 3,
// Bloque 3.1) a mensajes legibles. Extraido como funcion pura (sin JSX) para
// poder probarse con vitest sin necesitar un entorno DOM (ver
// frontend/vitest.config.ts: entorno "node", sin jsdom/testing-library).
export const GOOGLE_ERROR_MESSAGES: Record<string, string> = {
  INVALID_STATE: "El enlace de autorización ya no es válido. Intenta conectar nuevamente.",
  STATE_EXPIRED: "La autorización expiró antes de completarse (10 minutos). Intenta nuevamente.",
  AUTHORIZATION_DENIED: "Se canceló la autorización en Google.",
  REFRESH_TOKEN_REQUIRED:
    "Google no otorgó los permisos necesarios para mantener la conexión. Intenta nuevamente.",
  GOOGLE_ACCOUNT_MISMATCH:
    "La cuenta de Google usada no coincide con la ya conectada. Desconecta primero si deseas cambiar de cuenta.",
  ACCOUNT_ALREADY_CONNECTED: "Esa cuenta de Google ya está conectada por otro usuario de este despacho.",
  INVALID_EMAIL_NOT_VERIFIED: "La cuenta de Google usada no tiene el correo verificado.",
  TOKEN_EXCHANGE_FAILED: "No se pudo completar la autorización con Google. Intenta nuevamente.",
  USERINFO_FAILED: "No se pudo verificar la identidad de la cuenta de Google. Intenta nuevamente.",
  UNRECOVERABLE_ERROR: "Ocurrió un error inesperado. Intenta nuevamente.",
};

const _FALLBACK_MESSAGE = "No se pudo completar la conexión con Google.";

export function describeGoogleError(code: string | null | undefined): string {
  if (!code) return _FALLBACK_MESSAGE;
  return GOOGLE_ERROR_MESSAGES[code] ?? _FALLBACK_MESSAGE;
}
