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

// Destinos CONOCIDOS (M3). El frontend solo envia este enum: nunca URL, selector ni script. Conocido != habilitado: el catalogo del
// backend dice cuales estan validados en vivo (`enabled`).
export type SolDestination =
  | "MENU_SOL"
  | "FICHA_RUC"
  | "SIRE"
  | "SIRE_RVIE"
  | "SIRE_RCE"
  | "SIRE_GENERACION_REGISTROS"
  | "DECLARACIONES_Y_PAGOS"
  | "DECLARA_FACIL"
  | "TRAMITES_Y_CONSULTAS";

export interface SolDestinationInfo {
  id: SolDestination;
  label: string;
  group: string;
  validation: "LIVE_VALIDATED" | "NOT_VALIDATED" | string;
  enabled: boolean;
  // Evidencia estructural REAL del reconocimiento: COMPLETE | ARRIVAL_ONLY (falta la entrada de menu) | ALIAS | CONTAINED | NONE.
  evidence?: "COMPLETE" | "RECON_COMPLETE" | "LIVE_ACCEPTED" | "ARRIVAL_ONLY" | "ALIAS" | "CONTAINED" | "NONE" | string;
  requires_profile_proof?: boolean; // estructura validada, pero cada perfil/cliente necesita su propia prueba real (no se extrapola)
  alias_of?: SolDestination | null; // mismo destino funcional que otro (Tramites y Consultas == Menu SOL): no se duplica
  contained_in?: SolDestination | null; // se alcanza desde otro destino (Declara Facil dentro de Declaraciones y Pagos)
  open_only?: boolean; // solo apertura supervisada: toda operacion posterior queda bajo control humano
  offered?: boolean; // tarjeta propia en la UI (false: alias, contenido en otro destino o identificador historico)
  acceptance_eligible?: boolean; // puede probarse en vivo con una autorizacion del propietario (sin habilitarse globalmente)
}

// M3: aceptacion real controlada (una autorizacion = un cliente, un perfil, un destino, un solo uso).
export interface AcceptanceOut {
  id: string;
  client_id: string;
  credential_id: string;
  destination: SolDestination;
  status: "AUTHORIZED" | "CONSUMED" | "REVOKED" | "EXPIRED";
  created_at: string;
  expires_at: string;
  consumed_at: string | null;
  session_id: string | null;
  result: "PASS" | "FAIL" | null;
  result_note: string | null;
  /** Fase calculada por el SERVIDOR desde la sesion real; la UI nunca la deduce. */
  phase: AcceptancePhase;
  session_status: string | null;
  session_operation_result: string | null;
  diagnostic_code: string | null;
  result_source: "OWNER" | "SYSTEM" | null;
}

export type AcceptancePhase =
  | "AUTHORIZED"
  | "REVOKED"
  | "EXPIRED"
  | "CONSUMED_RUNNING"
  | "SESSION_FAILED"
  | "ARRIVAL_AWAITING_CONFIRMATION"
  | "CONFIRMED_PASS"
  | "CONFIRMED_FAIL";

export interface AcceptanceListOut {
  acceptances: AcceptanceOut[];
}

export interface SolDestinationsOut {
  destinations: SolDestination[]; // solo los habilitados (compatibilidad M2)
  catalog: SolDestinationInfo[];
}

// M3: cuatro estados que NUNCA son equivalentes (reconocido != habilitado != permitido para ESTA credencial != completado).
export interface SolDestinationPermission {
  destination: SolDestination;
  recognized: "COMPLETE" | "RECON_COMPLETE" | "LIVE_ACCEPTED" | "ARRIVAL_ONLY" | "ALIAS" | "CONTAINED" | "NONE" | string;
  enabled: boolean;
  permitted: "UNKNOWN" | "OBSERVED_ALLOWED" | "OBSERVED_NOT_IN_MENU";
  completed: boolean;
  last_session_id: string | null;
  proven?: boolean; // ESTE perfil (de este cliente) supero la aceptacion real del destino
  available?: boolean; // habilitado Y (sin prueba exigida o ya probado por este perfil): se puede ofrecer el boton de apertura
}

export interface SolCredentialPermissions {
  credential_id: string;
  profile_name: string;
  credential_kind: string;
  status: string;
  destinations: SolDestinationPermission[];
}

export interface SolCredentialDestinationsOut {
  credentials: SolCredentialPermissions[];
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
  credential_id?: string | null; // perfil con el que se abrio el acceso
  acceptance_test?: boolean; // prueba real controlada (destino aun no habilitado globalmente)
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
