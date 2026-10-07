// Cliente de la API del acceso SOL supervisado (Fase 5, Bloque 5.2, Domain F). El frontend habla SOLO con el backend
// (`/api/v1/sol-access/*`): nunca con el agente local ni con SUNAT. El estado de la sesion lo decide el backend.
import { apiClient } from "@/lib/apiClient";
import type { SolAccessSessionOut, SolAgentsOut } from "@/lib/types/solAccess";

export function fetchSolAgents(): Promise<SolAgentsOut> {
  return apiClient.get<SolAgentsOut>("/sol-access/agents");
}

export function fetchClientSolSession(clientId: string): Promise<SolAccessSessionOut | null> {
  return apiClient.get<SolAccessSessionOut | null>(`/sol-access/clients/${clientId}/session`);
}

export function fetchSolSession(sessionId: string): Promise<SolAccessSessionOut> {
  return apiClient.get<SolAccessSessionOut>(`/sol-access/sessions/${sessionId}`);
}

// `idempotencyKey` identifica UNA intencion del usuario: el backend devuelve la misma sesion logica ante reintentos.
export function createSolSession(clientId: string, idempotencyKey: string): Promise<SolAccessSessionOut> {
  return apiClient.post<SolAccessSessionOut>("/sol-access/sessions", {
    client_id: clientId,
    idempotency_key: idempotencyKey,
  });
}

export function cancelSolSession(sessionId: string): Promise<SolAccessSessionOut> {
  return apiClient.post<SolAccessSessionOut>(`/sol-access/sessions/${sessionId}/cancel`);
}
