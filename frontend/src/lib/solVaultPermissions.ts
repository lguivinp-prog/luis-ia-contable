// Ayudantes puros de gateo por permiso para la boveda SOL (Fase 5, Bloque
// 5.1). Nunca reconstruyen la matriz rol->permiso -- son envoltorios
// nombrados sobre `hasPermission`, para que la logica de que boton/seccion
// se muestra sea la MISMA funcion usada por la UI y por las pruebas (en vez
// de duplicar la cadena del permiso en cada sitio).
import { hasPermission } from "@/lib/permissions";
import type { User } from "@/lib/types/auth";

export function canViewSolVaultNav(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_vault:unlock");
}

export function canInitializeSolVault(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_vault:initialize");
}

export function canRecoverSolVault(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_vault:recover");
}

export function canRotateSolVault(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_vault:rotate");
}

export function canManageSolVaultUsers(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_vault:manage_users");
}

export function canReadSolCredential(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_credential:read");
}

export function canCreateSolCredential(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_credential:create");
}

export function canUpdateSolCredential(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_credential:update");
}

export function canDisableSolCredential(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_credential:disable");
}
