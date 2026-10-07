// Cliente de la API del cockpit mensual y del workspace cliente/periodo (M1). Los estados derivados los calcula el
// backend; los 3 estados manuales se escriben con PUT + `expected_row_version` (concurrencia optimista).
import { apiClient } from "@/lib/apiClient";
import { buildCockpitQueryString } from "@/lib/cockpitPresentation";
import type {
  CockpitOut,
  CockpitQuery,
  OperationsHistoryEntry,
  OperationsState,
  OperationsUpdate,
  Workspace,
} from "@/lib/types/cockpit";

export function fetchMonthlyCockpit(query: CockpitQuery): Promise<CockpitOut> {
  return apiClient.get<CockpitOut>(`/cockpit/monthly?${buildCockpitQueryString(query)}`);
}

const scopePath = (clientId: string, periodId: string) => `/cockpit/clients/${clientId}/periods/${periodId}`;

export function fetchWorkspace(clientId: string, periodId: string): Promise<Workspace> {
  return apiClient.get<Workspace>(scopePath(clientId, periodId));
}

export function updateOperations(clientId: string, periodId: string, payload: OperationsUpdate): Promise<OperationsState> {
  return apiClient.put<OperationsState>(`${scopePath(clientId, periodId)}/operations`, payload);
}

export function fetchOperationsHistory(clientId: string, periodId: string): Promise<OperationsHistoryEntry[]> {
  return apiClient.get<OperationsHistoryEntry[]>(`${scopePath(clientId, periodId)}/history`);
}
