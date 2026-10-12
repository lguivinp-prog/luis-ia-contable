// Cliente de la API de la Ficha RUC y del alta asistida (Producto M2). Habla SOLO con el backend; las credenciales SOL
// NUNCA pasan por aqui (se guardan por la Boveda existente: `createClientSolCredential`).
import { apiClient } from "@/lib/apiClient";
import type { CompleteOnboardingPayload, FichaOverview, FichaSnapshot, Onboarding } from "@/lib/types/fichaRuc";

export function fetchFichaOverview(clientId: string): Promise<FichaOverview> {
  return apiClient.get<FichaOverview>(`/clients/${clientId}/ficha-ruc/overview`);
}

export function listFichaSnapshots(clientId: string): Promise<FichaSnapshot[]> {
  return apiClient.get<FichaSnapshot[]>(`/clients/${clientId}/ficha-ruc/snapshots`);
}

export function fetchFichaSnapshot(clientId: string, snapshotId: string): Promise<FichaSnapshot> {
  return apiClient.get<FichaSnapshot>(`/clients/${clientId}/ficha-ruc/snapshots/${snapshotId}`);
}

export function acceptFichaSnapshot(
  clientId: string,
  snapshotId: string,
  acceptedFields: string[],
  expectedVersion: number,
): Promise<FichaSnapshot> {
  return apiClient.post<FichaSnapshot>(`/clients/${clientId}/ficha-ruc/snapshots/${snapshotId}/accept`, {
    accepted_fields: acceptedFields,
    expected_version: expectedVersion,
  });
}

export function rejectFichaSnapshot(clientId: string, snapshotId: string, expectedVersion: number): Promise<FichaSnapshot> {
  return apiClient.post<FichaSnapshot>(`/clients/${clientId}/ficha-ruc/snapshots/${snapshotId}/reject`, {
    expected_version: expectedVersion,
  });
}

// Solo el RUC: nunca usuario/clave SOL ni contraseña de la boveda.
export function startOnboarding(ruc: string): Promise<Onboarding> {
  return apiClient.post<Onboarding>("/client-onboardings", { ruc });
}

export function fetchOnboarding(onboardingId: string): Promise<Onboarding> {
  return apiClient.get<Onboarding>(`/client-onboardings/${onboardingId}`);
}

export function completeOnboarding(onboardingId: string, payload: CompleteOnboardingPayload): Promise<Onboarding> {
  return apiClient.post<Onboarding>(`/client-onboardings/${onboardingId}/complete`, payload);
}

export function cancelOnboarding(onboardingId: string): Promise<Onboarding> {
  return apiClient.post<Onboarding>(`/client-onboardings/${onboardingId}/cancel`);
}
