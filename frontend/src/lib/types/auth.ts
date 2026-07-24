// El backend es la unica fuente de verdad de los permisos (ver ADR-0050):
// `permissions` ya viene calculado por rol desde GET /auth/me -- el frontend
// nunca reconstruye la matriz rol->permisos, solo consulta esta lista.
export interface User {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "contador" | "asistente";
  permissions: string[];
  must_change_password: boolean;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
  is_current: boolean;
}

export interface SessionPolicy {
  inactivity_timeout_minutes: number;
  inactivity_warning_seconds: number;
  session_ttl_hours: number;
}
