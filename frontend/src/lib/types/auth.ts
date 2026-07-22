export interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
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
