import { describe, expect, it } from "vitest";

import { describeSolVaultError, extractErrorCode, SOL_VAULT_ERROR_MESSAGES } from "@/lib/solVaultErrors";

describe("extractErrorCode", () => {
  it("extrae el codigo de un detail-string plano", () => {
    expect(extractErrorCode("SOL_VAULT_BAD_PASSWORD")).toBe("SOL_VAULT_BAD_PASSWORD");
  });

  it("extrae el codigo de un detail-objeto {code: ...} (formato real del backend)", () => {
    expect(extractErrorCode({ code: "SOL_VAULT_LOCKED" })).toBe("SOL_VAULT_LOCKED");
  });

  it("devuelve null si el objeto no tiene campo code", () => {
    expect(extractErrorCode({ message: "algo" })).toBeNull();
  });

  it("devuelve null si code no es un string", () => {
    expect(extractErrorCode({ code: 123 })).toBeNull();
  });

  it("devuelve null para null/undefined", () => {
    expect(extractErrorCode(null)).toBeNull();
    expect(extractErrorCode(undefined)).toBeNull();
  });
});

describe("describeSolVaultError", () => {
  it("devuelve el mensaje especifico para cada codigo real de la taxonomia", () => {
    for (const [code, message] of Object.entries(SOL_VAULT_ERROR_MESSAGES)) {
      expect(describeSolVaultError({ code })).toBe(message);
    }
  });

  it("devuelve el mensaje generico para un codigo desconocido", () => {
    expect(describeSolVaultError({ code: "ALGO_NO_MAPEADO" })).toBe(
      "No se pudo completar la operación sobre la bóveda SOL.",
    );
  });

  it("devuelve el mensaje generico cuando el detail es null/undefined", () => {
    expect(describeSolVaultError(null)).toBe("No se pudo completar la operación sobre la bóveda SOL.");
    expect(describeSolVaultError(undefined)).toBe("No se pudo completar la operación sobre la bóveda SOL.");
  });

  it("nunca revela contenido crudo del backend fuera del catalogo curado", () => {
    // El mensaje mostrado siempre proviene del catalogo fijo -- nunca se
    // interpola texto libre recibido en el detail del error.
    const detail = { code: "SOL_VAULT_BAD_PASSWORD", extra: "contrasena-ficticia-filtrada" };
    expect(describeSolVaultError(detail)).not.toContain("contrasena-ficticia-filtrada");
  });
});
