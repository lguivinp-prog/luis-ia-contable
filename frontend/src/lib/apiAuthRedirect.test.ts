import { describe, expect, it } from "vitest";

import { isDomainUnauthorizedBody } from "@/lib/apiAuthRedirect";

describe("401 de dominio vs sesion vencida", () => {
  it("la contrasena de boveda incorrecta no cuenta como sesion vencida", () => {
    expect(isDomainUnauthorizedBody({ detail: { code: "SOL_VAULT_BAD_PASSWORD" } })).toBe(true);
  });
  it("cualquier otro 401 sigue tratandose como sesion vencida", () => {
    expect(isDomainUnauthorizedBody({ detail: "Not authenticated" })).toBe(false);
    expect(isDomainUnauthorizedBody({ detail: { code: "OTHER" } })).toBe(false);
    expect(isDomainUnauthorizedBody(null)).toBe(false);
  });
});
