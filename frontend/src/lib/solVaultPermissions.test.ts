import { describe, expect, it } from "vitest";

import {
  canCreateSolCredential,
  canDisableSolCredential,
  canInitializeSolVault,
  canManageSolVaultUsers,
  canReadSolCredential,
  canRecoverSolVault,
  canRotateSolVault,
  canUpdateSolCredential,
  canViewSolVaultNav,
} from "@/lib/solVaultPermissions";
import type { User } from "@/lib/types/auth";

// Matriz real (backend/app/core/permissions.py, Fase 5 Bloque 5.1):
// admin-only -> sol_vault:initialize/manage_users/recover/rotate.
// admin+contador -> sol_vault:unlock, sol_credential:read/create/update/disable.
// asistente -> ninguno de los 9 permisos nuevos.
function buildUser(role: User["role"], permissions: string[]): User {
  return {
    id: "3441b255-13f5-441f-a0ba-48db13b41157",
    email: `${role}@demo-ficticio.dev`,
    full_name: `${role} Demo`,
    role,
    permissions,
    must_change_password: false,
  };
}

const ADMIN_PERMISSIONS = [
  "sol_vault:initialize",
  "sol_vault:manage_users",
  "sol_vault:recover",
  "sol_vault:rotate",
  "sol_vault:unlock",
  "sol_credential:read",
  "sol_credential:create",
  "sol_credential:update",
  "sol_credential:disable",
];

const CONTADOR_PERMISSIONS = [
  "sol_vault:unlock",
  "sol_credential:read",
  "sol_credential:create",
  "sol_credential:update",
  "sol_credential:disable",
];

const ASISTENTE_PERMISSIONS: string[] = [];

describe("permisos de boveda SOL -- admin", () => {
  const admin = buildUser("admin", ADMIN_PERMISSIONS);

  it("ve la navegacion y tiene todas las capacidades admin-only", () => {
    expect(canViewSolVaultNav(admin)).toBe(true);
    expect(canInitializeSolVault(admin)).toBe(true);
    expect(canManageSolVaultUsers(admin)).toBe(true);
    expect(canRecoverSolVault(admin)).toBe(true);
    expect(canRotateSolVault(admin)).toBe(true);
  });

  it("tiene las 4 capacidades de credenciales SOL", () => {
    expect(canReadSolCredential(admin)).toBe(true);
    expect(canCreateSolCredential(admin)).toBe(true);
    expect(canUpdateSolCredential(admin)).toBe(true);
    expect(canDisableSolCredential(admin)).toBe(true);
  });
});

describe("permisos de boveda SOL -- contador", () => {
  const contador = buildUser("contador", CONTADOR_PERMISSIONS);

  it("ve la navegacion y las credenciales, pero ninguna capacidad admin-only", () => {
    expect(canViewSolVaultNav(contador)).toBe(true);
    expect(canReadSolCredential(contador)).toBe(true);
    expect(canCreateSolCredential(contador)).toBe(true);
    expect(canUpdateSolCredential(contador)).toBe(true);
    expect(canDisableSolCredential(contador)).toBe(true);

    expect(canInitializeSolVault(contador)).toBe(false);
    expect(canManageSolVaultUsers(contador)).toBe(false);
    expect(canRecoverSolVault(contador)).toBe(false);
    expect(canRotateSolVault(contador)).toBe(false);
  });
});

describe("permisos de boveda SOL -- asistente", () => {
  const asistente = buildUser("asistente", ASISTENTE_PERMISSIONS);

  it("no ve ningun acceso a la boveda ni a las credenciales SOL", () => {
    expect(canViewSolVaultNav(asistente)).toBe(false);
    expect(canInitializeSolVault(asistente)).toBe(false);
    expect(canManageSolVaultUsers(asistente)).toBe(false);
    expect(canRecoverSolVault(asistente)).toBe(false);
    expect(canRotateSolVault(asistente)).toBe(false);
    expect(canReadSolCredential(asistente)).toBe(false);
    expect(canCreateSolCredential(asistente)).toBe(false);
    expect(canUpdateSolCredential(asistente)).toBe(false);
    expect(canDisableSolCredential(asistente)).toBe(false);
  });
});

describe("permisos de boveda SOL -- usuario ausente", () => {
  it("nunca concede nada para null/undefined (fallo cerrado)", () => {
    expect(canViewSolVaultNav(null)).toBe(false);
    expect(canViewSolVaultNav(undefined)).toBe(false);
    expect(canReadSolCredential(null)).toBe(false);
  });
});
