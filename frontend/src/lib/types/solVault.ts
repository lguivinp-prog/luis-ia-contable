// Espejo de `backend/app/schemas/sol_vault.py` (Fase 5, Bloque 5.1, ver
// ADR-0068). Ningun tipo de este archivo modela una contrasena en claro,
// un ciphertext, un salt, una clave envuelta, ni el handle interno de
// `VaultUnlockRegistry` -- el backend nunca los devuelve.

export type SolVaultStatusValue = "NOT_INITIALIZED" | "ACTIVE" | "RECOVERY_REQUIRED" | "DISABLED";

export interface SolVaultStatusOut {
  initialized: boolean;
  vault_id: string | null;
  status: SolVaultStatusValue;
  unlocked: boolean;
  recovery_confirmed: boolean;
  version: number | null;
}

export interface InitializeVaultResponse {
  vault_id: string;
  status: string;
  recovery_code: string;
  message: string;
}

export interface RecoveryReplaceResponse {
  new_recovery_code: string;
  message: string;
}

export interface SimpleMessageResponse {
  message: string;
}

export type SlotKind = "USER" | "RECOVERY";
export type SlotStatus = "ACTIVE" | "REVOKED";

export interface SlotOut {
  id: string;
  kind: SlotKind;
  user_id: string | null;
  status: SlotStatus;
  recovery_confirmed_at: string | null;
  last_unlocked_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export type InvitationStatus = "PENDING" | "ACCEPTED" | "EXPIRED" | "CANCELLED";

export interface InvitationOut {
  id: string;
  vault_id: string;
  invited_by: string;
  target_user_id: string;
  status: InvitationStatus;
  expires_at: string;
  accepted_at: string | null;
  version: number;
  created_at: string;
}

export interface RotateDataKeyResponse {
  new_data_key_version: number;
  message: string;
}

// Coincide con `SolCredentialKind` de `app/models/client_sol_credential.py` --
// no existe un valor "PRIMARY" generico: la credencial principal distingue
// si usa el RUC propio del contribuyente o si es una excepcion autorizada.
export type SolCredentialKind = "SECONDARY" | "PRIMARY_OWN_RUC" | "PRIMARY_EXCEPTIONAL";
export type SolCredentialStatus = "NEEDS_VALIDATION" | "ACTIVE" | "INVALID" | "DISABLED";

export interface SolCredentialStatusOut {
  id: string;
  client_id: string;
  credential_kind: SolCredentialKind;
  status: SolCredentialStatus;
  last_validation_result: string | null;
  last_validated_at: string | null;
  last_used_at: string | null;
  authorization_basis: string;
  authorized_at: string;
  authorization_expires_at: string | null;
  authorization_reference: string | null;
  authorized_scope_description: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CreateSolCredentialPayload {
  credential_kind: SolCredentialKind;
  sol_username: string;
  sol_password: string;
  authorization_basis: string;
  authorization_expires_at?: string | null;
  authorization_reference?: string | null;
  authorized_scope_description?: string | null;
}

export interface UpdateSolCredentialPayload {
  expected_version: number;
  sol_username?: string;
  sol_password?: string;
  authorization_basis?: string;
  authorization_expires_at?: string | null;
  authorization_reference?: string | null;
  authorized_scope_description?: string | null;
}
