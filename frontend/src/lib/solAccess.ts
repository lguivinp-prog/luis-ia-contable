// Cliente de la API del acceso SOL supervisado (Fase 5, Bloque 5.2, Domain F). El frontend habla SOLO con el backend
// (`/api/v1/sol-access/*`): nunca con el agente local ni con SUNAT. El estado de la sesion lo decide el backend.
import { apiClient } from "@/lib/apiClient";
import type {
  AcceptanceListOut,
  AcceptanceOut,
  SolAccessSessionOut,
  SolAgentsOut,
  SolCredentialDestinationsOut,
  SolDestination,
  SolDestinationsOut,
} from "@/lib/types/solAccess";

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
  /** Periodo del workspace mensual desde el que se abre el acceso (solo auditoria; no cambia estados de cumplimiento). */
  originTaxPeriodId?: string;
  /** Perfil de credencial a usar. Obligatorio cuando el cliente tiene varios perfiles: el servidor nunca elige ni reintenta con otro. */
  credentialId?: string;
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
  if (options.originTaxPeriodId) body.origin_tax_period_id = options.originTaxPeriodId;
  if (options.credentialId) body.credential_id = options.credentialId;
  return apiClient.post<SolAccessSessionOut>("/sol-access/sessions", body);
}

export function cancelSolSession(sessionId: string): Promise<SolAccessSessionOut> {
  return apiClient.post<SolAccessSessionOut>(`/sol-access/sessions/${sessionId}/cancel`);
}

// Por perfil y destino: reconocido / habilitado / permitido (observado en sesiones reales) / completado.
export function fetchCredentialDestinations(clientId: string): Promise<SolCredentialDestinationsOut> {
  return apiClient.get<SolCredentialDestinationsOut>(`/sol-access/clients/${clientId}/credential-destinations`);
}

// --- M3: aceptacion real controlada (solo propietario/administrador) ---
export function fetchAcceptances(clientId: string): Promise<AcceptanceListOut> {
  return apiClient.get<AcceptanceListOut>(`/sol-access/clients/${clientId}/acceptances`);
}

export function grantAcceptance(clientId: string, credentialId: string, destination: SolDestination): Promise<AcceptanceOut> {
  return apiClient.post<AcceptanceOut>("/sol-access/acceptances", {
    client_id: clientId,
    credential_id: credentialId,
    destination,
    confirm_real_access: true,
  });
}

export function revokeAcceptance(acceptanceId: string): Promise<AcceptanceOut> {
  return apiClient.post<AcceptanceOut>(`/sol-access/acceptances/${acceptanceId}/revoke`);
}

export function recordAcceptanceResult(acceptanceId: string, result: "PASS" | "FAIL", note: string): Promise<AcceptanceOut> {
  return apiClient.post<AcceptanceOut>(`/sol-access/acceptances/${acceptanceId}/result`, { result, note: note.trim() || null });
}
