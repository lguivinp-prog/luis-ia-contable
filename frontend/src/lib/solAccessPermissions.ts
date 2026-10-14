// Gateo por permiso del acceso SOL supervisado (Fase 5, Bloque 5.2, Domain F). Solo UX: el backend es la frontera de
// seguridad (RBAC real). Envoltorios nombrados sobre `hasPermission`, sin reconstruir la matriz rol->permiso.
import { hasPermission } from "@/lib/permissions";
import type { User } from "@/lib/types/auth";

export function canViewSupervisedSolAccess(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_session:read");
}

// Iniciar exige crear la sesion y poder leer la credencial (el backend aplica ambos).
export function canStartSolSession(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_session:create") && hasPermission(user, "sol_credential:read");
}

export function canCancelSolSession(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_session:cancel");
}

export function canViewSolAgents(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_agent:read");
}

// Aceptacion real controlada de destinos (M3): SOLO el propietario/administrador. El backend lo vuelve a exigir.
export function canManageAcceptance(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_acceptance:manage");
}
