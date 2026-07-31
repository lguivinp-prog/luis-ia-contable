import { describe, expect, it } from "vitest";

import { describeGoogleError, GOOGLE_ERROR_MESSAGES } from "@/lib/googleIntegrationErrors";

describe("describeGoogleError", () => {
  it("devuelve el mensaje especifico para un codigo conocido", () => {
    expect(describeGoogleError("GOOGLE_ACCOUNT_MISMATCH")).toContain("no coincide con la ya conectada");
  });

  it("devuelve el mensaje generico para un codigo desconocido", () => {
    expect(describeGoogleError("ALGO_NO_MAPEADO")).toBe("No se pudo completar la conexión con Google.");
  });

  it("devuelve el mensaje generico cuando el codigo es null/undefined", () => {
    expect(describeGoogleError(null)).toBe("No se pudo completar la conexión con Google.");
    expect(describeGoogleError(undefined)).toBe("No se pudo completar la conexión con Google.");
  });

  it("nunca usa la palabra 'enviado' o 'abierto' sin calificar (mensajes honestos)", () => {
    for (const message of Object.values(GOOGLE_ERROR_MESSAGES)) {
      expect(message.toLowerCase()).not.toMatch(/\bmensaje enviado\b/);
      expect(message.toLowerCase()).not.toMatch(/\bwhatsapp abierto\b/);
    }
  });

  it("cubre todos los codigos de la taxonomia del Bloque 3.1", () => {
    const expectedCodes = [
      "INVALID_STATE",
      "STATE_EXPIRED",
      "AUTHORIZATION_DENIED",
      "REFRESH_TOKEN_REQUIRED",
      "GOOGLE_ACCOUNT_MISMATCH",
      "ACCOUNT_ALREADY_CONNECTED",
      "INVALID_EMAIL_NOT_VERIFIED",
      "TOKEN_EXCHANGE_FAILED",
      "USERINFO_FAILED",
      "UNRECOVERABLE_ERROR",
    ];
    for (const code of expectedCodes) {
      expect(GOOGLE_ERROR_MESSAGES).toHaveProperty(code);
    }
  });
});
