// Politica de contrasena personal de boveda SOL (Fase 5, Bloque 5.1, ver
// ADR-0068, Seccion 19 del encargo): espejo exacto de
// `_validate_vault_password` en `app/schemas/sol_vault.py` -- minimo 14
// caracteres, maximo 256, se permiten frases. Deliberadamente SIN exigir
// simbolos/mayusculas/numeros arbitrarios (distinta de la politica de login).
export const VAULT_PASSWORD_MIN_LENGTH = 14;
export const VAULT_PASSWORD_MAX_LENGTH = 256;

export function isValidVaultPassword(password: string): boolean {
  return password.length >= VAULT_PASSWORD_MIN_LENGTH && password.length <= VAULT_PASSWORD_MAX_LENGTH;
}
