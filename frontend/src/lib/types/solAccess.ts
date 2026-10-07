// Espejo de `backend/app/schemas/sol_access.py` (Fase 5, Bloque 5.2, Domain F). Ningun tipo de este archivo
// modela credenciales, RUC, usuario/clave SOL, tokens ni huellas de certificado: el backend nunca los devuelve.

export type SolSessionStatus =
  | "CREATED"
  | "WAITING_FOR_AGENT"
  | "WAITING_FOR_USER"
  | "IN_PROGRESS"
  | "SUCCEEDED"
  | "PARTIAL_FAILURE"
  | "FAILED"
  | "CANCELLED"
  | "EXPIRED";

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
