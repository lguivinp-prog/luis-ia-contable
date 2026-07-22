import { apiClient } from "@/lib/apiClient";
import type { User } from "@/lib/types/auth";

export function login(email: string, password: string): Promise<User> {
  return apiClient.post<User>("/auth/login", { email, password });
}

export function logout(): Promise<void> {
  return apiClient.post<void>("/auth/logout");
}

export function fetchCurrentUser(): Promise<User> {
  return apiClient.get<User>("/auth/me");
}
