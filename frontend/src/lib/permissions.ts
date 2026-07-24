import type { User } from "@/lib/types/auth";

// La lista de permisos la calcula el backend (ver ADR-0050, `GET /auth/me`).
// Este archivo nunca reconstruye la matriz rol->permisos: solo consulta la
// lista ya calculada que trae `User.permissions`.
export function hasPermission(user: User | null | undefined, permission: string): boolean {
  return user?.permissions.includes(permission) ?? false;
}
