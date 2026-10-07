import { describe, expect, it } from "vitest";

import { MOBILE_NAV_BREAKPOINT_PX, activeNavHref, buildNavGroups, isNavItemActive } from "@/lib/navigation";
import type { User } from "@/lib/types/auth";

const user = (permissions: string[]) => ({ permissions }) as unknown as User;
const ADMIN = user([
  "dashboard:read", "task:read", "communication:draft", "user:read", "integration:connect", "sol_vault:unlock",
]);
const hrefs = (u: User | null) => buildNavGroups(u).flatMap((g) => g.items.map((i) => i.href));

describe("navegacion agrupada", () => {
  it("agrupa en Operacion, Clientes, Comunicaciones, Reportes y Configuracion", () => {
    expect(buildNavGroups(ADMIN).map((g) => g.label)).toEqual([
      "Operación", "Clientes", "Comunicaciones", "Reportes", "Configuración",
    ]);
  });

  it("conserva TODAS las URLs existentes", () => {
    const all = hrefs(ADMIN);
    for (const href of [
      "/cockpit", "/dashboard", "/agenda", "/tasks", "/clients", "/communications", "/reports", "/tax-categories",
      "/tax-schedules", "/tax-periods", "/declarations/generate", "/users", "/settings/integrations",
      "/settings/sol-vault", "/security",
    ]) {
      expect(all).toContain(href);
    }
  });

  it("no inventa destinos: sin pagina de alertas, sin enlaces SUNAT", () => {
    for (const href of hrefs(ADMIN)) {
      expect(href).not.toMatch(/alert|sunat|buzon|sire|ficha/i);
    }
  });

  it("respeta permisos: sin ellos desaparecen los grupos/items correspondientes", () => {
    const limited = hrefs(user([]));
    for (const href of ["/cockpit", "/agenda", "/tasks", "/communications", "/users", "/settings/integrations", "/settings/sol-vault"]) {
      expect(limited).not.toContain(href);
    }
    expect(buildNavGroups(user([])).map((g) => g.id)).not.toContain("comunicaciones");
    expect(hrefs(null)).toContain("/security");
  });

  it("marca como activo el item mas especifico (el workspace pertenece al cockpit)", () => {
    const groups = buildNavGroups(ADMIN);
    expect(activeNavHref("/cockpit/abc", groups)).toBe("/cockpit");
    expect(activeNavHref("/declarations/generate", groups)).toBe("/declarations/generate");
    expect(activeNavHref("/clients/1/fees", groups)).toBe("/clients");
    expect(activeNavHref("/otra", groups)).toBeNull();
    expect(isNavItemActive("/clients2", "/clients")).toBe(false);
  });

  it("el punto de quiebre movil es 768px", () => {
    expect(MOBILE_NAV_BREAKPOINT_PX).toBe(768);
  });
});
