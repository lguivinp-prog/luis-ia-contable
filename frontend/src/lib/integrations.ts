import { apiClient } from "@/lib/apiClient";
import type {
  GoogleAuthorizeResponse,
  GoogleConnectionStatus,
  GoogleDisconnectResponse,
} from "@/lib/types/integrations";

export function fetchGoogleStatus(): Promise<GoogleConnectionStatus> {
  return apiClient.get<GoogleConnectionStatus>("/integrations/google/status");
}

// Bloque 3.1: `capability` solo admite "IDENTITY" -- el backend rechaza
// cualquier otro valor con 422 hasta que los Bloques 3.2/3.3 lo habiliten.
export function startGoogleAuthorization(returnPath: string): Promise<GoogleAuthorizeResponse> {
  return apiClient.post<GoogleAuthorizeResponse>("/integrations/google/authorize", {
    capability: "IDENTITY",
    return_path: returnPath,
  });
}

export function disconnectGoogle(): Promise<GoogleDisconnectResponse> {
  return apiClient.post<GoogleDisconnectResponse>("/integrations/google/disconnect");
}
