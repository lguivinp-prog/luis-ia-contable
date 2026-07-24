import { apiClient } from "@/lib/apiClient";
import type { ManagedUser, Role } from "@/lib/types/user";

export function listUsers(): Promise<ManagedUser[]> {
  return apiClient.get<ManagedUser[]>("/users");
}

export function createUser(payload: {
  email: string;
  full_name: string;
  role: Role;
  password: string;
}): Promise<ManagedUser> {
  return apiClient.post<ManagedUser>("/users", payload);
}

export function changeUserRole(userId: string, role: Role, reason: string): Promise<ManagedUser> {
  return apiClient.patch<ManagedUser>(`/users/${userId}/role`, { role, reason });
}

export function deactivateUser(userId: string, reason: string): Promise<ManagedUser> {
  return apiClient.patch<ManagedUser>(`/users/${userId}/deactivate`, { reason });
}

export function reactivateUser(userId: string): Promise<ManagedUser> {
  return apiClient.patch<ManagedUser>(`/users/${userId}/reactivate`);
}

export function resetUserPassword(
  userId: string,
  newPassword: string,
  reason: string,
): Promise<ManagedUser> {
  return apiClient.patch<ManagedUser>(`/users/${userId}/reset-password`, {
    new_password: newPassword,
    reason,
  });
}
