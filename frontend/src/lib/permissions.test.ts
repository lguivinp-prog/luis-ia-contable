import { describe, expect, it } from "vitest";

import { hasPermission } from "@/lib/permissions";
import type { User } from "@/lib/types/auth";

function buildUser(permissions: string[]): User {
  return {
    id: "3441b255-13f5-441f-a0ba-48db13b41157",
    email: "admin@demo-ficticio.dev",
    full_name: "Admin Demo",
    role: "admin",
    permissions,
    must_change_password: false,
  };
}

describe("hasPermission", () => {
  it("devuelve true cuando el permiso esta en la lista del usuario", () => {
    const user = buildUser(["client:read", "client:write"]);
    expect(hasPermission(user, "client:write")).toBe(true);
  });

  it("devuelve false cuando el permiso no esta en la lista del usuario", () => {
    const user = buildUser(["client:read"]);
    expect(hasPermission(user, "user:manage")).toBe(false);
  });

  it("devuelve false para un usuario null", () => {
    expect(hasPermission(null, "client:read")).toBe(false);
  });

  it("devuelve false para un usuario undefined", () => {
    expect(hasPermission(undefined, "client:read")).toBe(false);
  });

  it("no reconstruye la matriz por rol -- solo consulta la lista ya calculada", () => {
    // Un usuario "asistente" con permisos explicitamente listados (como los
    // entrega el backend real) se evalua exactamente igual que cualquier otro.
    const user: User = {
      id: "975b3161-d119-4bff-82ad-05804150bb0c",
      email: "asistente@demo-ficticio.dev",
      full_name: "Asistente Demo",
      role: "asistente",
      permissions: ["client:read", "document:item_write", "report:export"],
      must_change_password: false,
    };
    expect(hasPermission(user, "document:item_write")).toBe(true);
    expect(hasPermission(user, "document:manage")).toBe(false);
  });
});
