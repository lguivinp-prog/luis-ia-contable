// Espejo de `backend/app/schemas/sol_access.py` (Fase 5, Bloque 5.2, Domain F). Ningun tipo de este archivo
// modela credenciales, RUC, usuario/clave SOL, tokens ni huellas de certificado: el backend nunca los devuelve.

export type SolSessionStatus =
  | "CREATED"
  | "WAITING_FOR_AGENT"
  | "WAITING_FOR_USER"
  | "IN_PROGRESS"
  | "HUMAN_CONTROL"
  | "SUCCEEDED"
  | "PARTIAL_FAILURE"
  | "FAILED"
  | "CANCELLED"
  | "EXPIRED";

// Allowlist cerrada de destinos LIVE-VALIDADOS (M2). El frontend solo envia este enum: nunca URL, selector ni script.
export type SolDestination = "MENU_SOL" | "FICHA_RUC";

export interface SolDestinationsOut {
  destinations: SolDestination[];
}

export interface SolAccessSessionOut {
  id: string;
  client_id: string;
  scope: string;
  status: SolSessionStatus | string;
  terminal_reason_code: string | null;
  operation_result: string | null;
  created_at: string;
  started_at: string | null;
  waiting_for_user_at: string | null;
  completed_at: string | null;
  expires_at: string;
  version: number;
  is_terminal: boolean;
  destination: SolDestination | string | null;
  extract_ficha: boolean;
  human_control_at: string | null;
  /** Codigo de diagnostico estructural del fallo (lista cerrada; nunca texto del portal). */
  diagnostic_code?: string | null;
}

export interface SolAgentOut {
  id: string;
  display_name: string;
  status: string;
  paired_at: string;
  last_seen_at: string | null;
  available: boolean;
}

export interface SolAgentsOut {
  broker_enabled: boolean;
  agents: SolAgentOut[];
}
