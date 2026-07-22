import { apiClient } from "@/lib/apiClient";
import type { SessionInfo, SessionPolicy, User } from "@/lib/types/auth";

export function login(email: string, password: string): Promise<User> {
  return apiClient.post<User>("/auth/login", { email, password });
}

export function logout(): Promise<void> {
  return apiClient.post<void>("/auth/logout");
}

export function fetchCurrentUser(): Promise<User> {
  return apiClient.get<User>("/auth/me");
}

export function fetchSessionPolicy(): Promise<SessionPolicy> {
  return apiClient.get<SessionPolicy>("/auth/session-policy");
}

export function keepSessionAlive(): Promise<{ status: string; last_seen_at: string }> {
  return apiClient.post("/auth/keep-alive");
}

export function listSessions(): Promise<SessionInfo[]> {
  return apiClient.get<SessionInfo[]>("/auth/sessions");
}

export function revokeOtherSessions(): Promise<{ status: string; revoked_count: number }> {
  return apiClient.post("/auth/sessions/revoke-others");
}

export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return apiClient.post<void>("/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}
