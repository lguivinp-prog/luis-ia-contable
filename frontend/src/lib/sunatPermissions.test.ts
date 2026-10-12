import { describe, expect, it } from "vitest";

import { canOnboardAssisted, canOpenSunatDestination, canRefreshFicha, canReviewFicha } from "@/lib/sunatPermissions";
import type { User } from "@/lib/types/auth";

const user = (permissions: string[]) => ({ permissions }) as unknown as User;

// Conjuntos que reflejan la matriz real del backend (el backend sigue siendo la autoridad).
const ASISTENTE = user(["client:read", "dashboard:read", "task:read", "document:read", "communication:draft"]);
const CONTADOR = user([
  "client:read", "client:write", "sol_session:create", "sol_session:read", "sol_session:cancel", "sol_credential:read",
  "sol_credential:create", "sol_credential:update",
]);

describe("permisos SUNAT (composicion de permisos existentes)", () => {
  it("el contador puede abrir destinos, actualizar la Ficha, revisarla y hacer el alta asistida", () => {
    expect(canOpenSunatDestination(CONTADOR)).toBe(true);
    expect(canRefreshFicha(CONTADOR)).toBe(true);
    expect(canReviewFicha(CONTADOR)).toBe(true);
    expect(canOnboardAssisted(CONTADOR)).toBe(true);
  });

  it("el asistente no puede nada que toque credenciales SOL ni editar clientes", () => {
    expect(canOpenSunatDestination(ASISTENTE)).toBe(false);
    expect(canRefreshFicha(ASISTENTE)).toBe(false);
    expect(canReviewFicha(ASISTENTE)).toBe(false);
    expect(canOnboardAssisted(ASISTENTE)).toBe(false);
  });

  it("cada permiso es necesario por separado", () => {
    expect(canRefreshFicha(user(["sol_session:create", "sol_credential:read"]))).toBe(false); // falta client:write
    expect(canOpenSunatDestination(user(["sol_session:create"]))).toBe(false);
    expect(canOnboardAssisted(user(["client:write", "sol_session:create", "sol_credential:read"]))).toBe(false); // falta crear credencial
    expect(canOnboardAssisted(null)).toBe(false);
  });
});
