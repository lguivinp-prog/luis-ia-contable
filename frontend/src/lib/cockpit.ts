// Cliente de la API del cockpit mensual (M1, Domain B): solo lectura, solo backend.
import { apiClient } from "@/lib/apiClient";
import { buildCockpitQueryString } from "@/lib/cockpitPresentation";
import type { CockpitOut, CockpitQuery } from "@/lib/types/cockpit";

export function fetchMonthlyCockpit(query: CockpitQuery): Promise<CockpitOut> {
  return apiClient.get<CockpitOut>(`/cockpit/monthly?${buildCockpitQueryString(query)}`);
}
