// Cliente de la API del acceso SOL supervisado (Fase 5, Bloque 5.2, Domain F). El frontend habla SOLO con el backend
// (`/api/v1/sol-access/*`): nunca con el agente local ni con SUNAT. El estado de la sesion lo decide el backend.
import { apiClient } from "@/lib/apiClient";
import type { SolAccessSessionOut, SolAgentsOut, SolDestination, SolDestinationsOut } from "@/lib/types/solAccess";

export function fetchSolAgents(): Promise<SolAgentsOut> {
  return apiClient.get<SolAgentsOut>("/sol-access/agents");
}

export function fetchClientSolSession(clientId: string): Promise<SolAccessSessionOut | null> {
  return apiClient.get<SolAccessSessionOut | null>(`/sol-access/clients/${clientId}/session`);
}

export function fetchSolSession(sessionId: string): Promise<SolAccessSessionOut> {
  return apiClient.get<SolAccessSessionOut>(`/sol-access/sessions/${sessionId}`);
}

// Destinos habilitados por el SERVIDOR (los no validados en vivo no figuran y la UI no los muestra).
export function fetchSolDestinations(): Promise<SolDestinationsOut> {
  return apiClient.get<SolDestinationsOut>("/sol-access/destinations");
}

export interface CreateSessionOptions {
  destination?: SolDestination;
  extractFicha?: boolean;
}

// `idempotencyKey` identifica UNA intencion del usuario: el backend devuelve la misma sesion logica ante reintentos.
// `destination` es SOLO el enum cerrado: jamas una URL, un selector ni codigo.
export function createSolSession(
  clientId: string,
  idempotencyKey: string,
  options: CreateSessionOptions = {},
): Promise<SolAccessSessionOut> {
  const body: Record<string, unknown> = { client_id: clientId, idempotency_key: idempotencyKey };
  if (options.destination) body.destination = options.destination;
  if (options.extractFicha) body.extract_ficha = true;
  return apiClient.post<SolAccessSessionOut>("/sol-access/sessions", body);
}

export function cancelSolSession(sessionId: string): Promise<SolAccessSessionOut> {
  return apiClient.post<SolAccessSessionOut>(`/sol-access/sessions/${sessionId}/cancel`);
}
