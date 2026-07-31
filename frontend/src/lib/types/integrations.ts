export interface GoogleConnectionStatus {
  configured: boolean;
  connected: boolean;
  status: "ACTIVE" | "NEEDS_REAUTH" | "DISCONNECTED" | null;
  google_account_email: string | null;
  identity_authorized: boolean;
  calendar_authorized: boolean;
  drive_authorized: boolean;
  authorized_at: string | null;
  reauth_reason: string | null;
}

export interface GoogleAuthorizeResponse {
  authorization_url: string;
  attempt_id: string;
}

export interface GoogleDisconnectResponse {
  disconnected: boolean;
  revocation_succeeded: boolean;
}
