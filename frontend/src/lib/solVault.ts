import { apiClient } from "@/lib/apiClient";
import type {
  CreateSolCredentialPayload,
  InitializeVaultResponse,
  InvitationOut,
  RecoveryReplaceResponse,
  RotateDataKeyResponse,
  SimpleMessageResponse,
  SlotOut,
  SolCredentialStatusOut,
  SolVaultStatusOut,
  UpdateSolCredentialPayload,
} from "@/lib/types/solVault";

export function fetchSolVaultStatus(): Promise<SolVaultStatusOut> {
  return apiClient.get<SolVaultStatusOut>("/sol-vault/status");
}

export function initializeSolVault(personalVaultPassword: string): Promise<InitializeVaultResponse> {
  return apiClient.post<InitializeVaultResponse>("/sol-vault/initialize", {
    personal_vault_password: personalVaultPassword,
  });
}

export function confirmSolVaultRecovery(recoveryCode: string): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>("/sol-vault/recovery/confirm", { recovery_code: recoveryCode });
}

export function replaceSolVaultRecovery(expectedVersion: number): Promise<RecoveryReplaceResponse> {
  return apiClient.post<RecoveryReplaceResponse>("/sol-vault/recovery/replace", {
    expected_version: expectedVersion,
  });
}

export function recoverSolVault(
  recoveryCode: string,
  newPersonalVaultPassword: string,
): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>("/sol-vault/recover", {
    recovery_code: recoveryCode,
    new_personal_vault_password: newPersonalVaultPassword,
  });
}

export function unlockSolVault(personalVaultPassword: string): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>("/sol-vault/unlock", {
    personal_vault_password: personalVaultPassword,
  });
}

export function lockSolVault(): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>("/sol-vault/lock");
}

export function changeSolVaultPassword(newPersonalVaultPassword: string): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>("/sol-vault/change-password", {
    new_personal_vault_password: newPersonalVaultPassword,
  });
}

export function rotateSolVaultDataKey(expectedVersion: number): Promise<RotateDataKeyResponse> {
  return apiClient.post<RotateDataKeyResponse>("/sol-vault/data-key/rotate", {
    expected_version: expectedVersion,
  });
}

export function listSolVaultSlots(): Promise<{ items: SlotOut[] }> {
  return apiClient.get<{ items: SlotOut[] }>("/sol-vault/slots");
}

export function revokeSolVaultSlot(slotId: string, expectedVersion: number): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>(`/sol-vault/slots/${slotId}/revoke`, {
    expected_version: expectedVersion,
  });
}

export function createSolVaultInvitation(targetUserId: string): Promise<InvitationOut> {
  return apiClient.post<InvitationOut>("/sol-vault/invitations", { target_user_id: targetUserId });
}

export function acceptSolVaultInvitation(
  invitationId: string,
  newPersonalVaultPassword: string,
): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>(`/sol-vault/invitations/${invitationId}/accept`, {
    new_personal_vault_password: newPersonalVaultPassword,
  });
}

export function reauthorizeSolVaultInvitation(
  invitationId: string,
  expectedVersion: number,
): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>(`/sol-vault/invitations/${invitationId}/reauthorize`, {
    expected_version: expectedVersion,
  });
}

export function cancelSolVaultInvitation(
  invitationId: string,
  expectedVersion: number,
): Promise<SimpleMessageResponse> {
  return apiClient.post<SimpleMessageResponse>(`/sol-vault/invitations/${invitationId}/cancel`, {
    expected_version: expectedVersion,
  });
}

export function fetchClientSolCredentialStatus(clientId: string): Promise<SolCredentialStatusOut | null> {
  return apiClient.get<SolCredentialStatusOut | null>(`/clients/${clientId}/sol-credential/status`);
}

export function createClientSolCredential(
  clientId: string,
  payload: CreateSolCredentialPayload,
): Promise<SolCredentialStatusOut> {
  return apiClient.post<SolCredentialStatusOut>(`/clients/${clientId}/sol-credential`, payload);
}

export function updateClientSolCredential(
  clientId: string,
  payload: UpdateSolCredentialPayload,
): Promise<SolCredentialStatusOut> {
  return apiClient.put<SolCredentialStatusOut>(`/clients/${clientId}/sol-credential`, payload);
}

export function disableClientSolCredential(
  clientId: string,
  expectedVersion: number,
): Promise<SolCredentialStatusOut> {
  return apiClient.post<SolCredentialStatusOut>(`/clients/${clientId}/sol-credential/disable`, {
    expected_version: expectedVersion,
  });
}

// --- M3: perfiles de credencial (varias credenciales cifradas por cliente; la seleccion es SIEMPRE explicita por id) ---

export function fetchClientSolCredentials(clientId: string): Promise<SolCredentialStatusOut[]> {
  return apiClient.get<SolCredentialStatusOut[]>(`/clients/${clientId}/sol-credentials`);
}

export function createClientSolCredentialProfile(
  clientId: string,
  payload: CreateSolCredentialPayload,
): Promise<SolCredentialStatusOut> {
  return apiClient.post<SolCredentialStatusOut>(`/clients/${clientId}/sol-credentials`, payload);
}

export function updateClientSolCredentialProfile(
  clientId: string,
  credentialId: string,
  payload: UpdateSolCredentialPayload,
): Promise<SolCredentialStatusOut> {
  return apiClient.put<SolCredentialStatusOut>(`/clients/${clientId}/sol-credentials/${credentialId}`, payload);
}

export function disableClientSolCredentialProfile(
  clientId: string,
  credentialId: string,
  expectedVersion: number,
): Promise<SolCredentialStatusOut> {
  return apiClient.post<SolCredentialStatusOut>(`/clients/${clientId}/sol-credentials/${credentialId}/disable`, {
    expected_version: expectedVersion,
  });
}
