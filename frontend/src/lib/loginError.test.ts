import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/apiClient";
import { describeLoginError } from "@/lib/loginError";

describe("describeLoginError", () => {
  it("distingue contraseña incorrecta de indisponibilidad tecnica", () => {
    expect(describeLoginError(new ApiError(401, "x"))).toBe("Correo o contraseña incorrectos.");
    for (const status of [500, 502, 503]) {
      const msg = describeLoginError(new ApiError(status, "x"));
      expect(msg).toContain("no está disponible");
      expect(msg).toContain("No es un problema de tu contraseña");
      expect(msg).not.toContain("incorrectos");
    }
    expect(describeLoginError(new TypeError("fetch failed"))).toContain("No se pudo contactar");
    expect(describeLoginError(new ApiError(429, "Demasiados intentos"))).toBe("Demasiados intentos");
  });
});
