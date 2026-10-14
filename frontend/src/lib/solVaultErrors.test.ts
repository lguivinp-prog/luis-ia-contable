import { describe, expect, it } from "vitest";

import { describeSolVaultError, extractErrorCode, findPersistedProfile, SOL_VAULT_ERROR_MESSAGES, validateCredentialForm } from "@/lib/solVaultErrors";

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

describe("registro de perfiles SOL: validacion previa y fallos ambiguos", () => {
  const ok = { profileName: "SOL Principal", authorizationBasis: "Autorizado por el titular", authorizationReference: "", authorizedScopeDescription: "" };

  it("acepta valores dentro de los limites reales y rechaza el motivo largo con un mensaje util (la causa real de la incidencia)", () => {
    expect(validateCredentialForm(ok)).toBeNull();
    const long = validateCredentialForm({ ...ok, authorizationBasis: "x".repeat(130) });
    expect(long).toContain("hasta 60 caracteres");
    expect(long).toContain("Descripción del alcance");
    expect(validateCredentialForm({ ...ok, profileName: "y".repeat(61) })).toContain("nombre interno");
  });

  it("interpreta el 422 de FastAPI sin reproducir el valor enviado", () => {
    const detail = [{ loc: ["body", "authorization_basis"], type: "string_too_long", ctx: { max_length: 60 }, input: "[redacted]" }];
    const message = describeSolVaultError(detail);
    expect(message).toContain("motivo de autorización");
    expect(message).toContain("60");
    expect(describeSolVaultError([{ loc: ["body", "otra"], type: "x" }])).toContain("Revisa los datos");
  });

  it("los codigos nuevos tienen mensaje propio (no el generico)", () => {
    expect(describeSolVaultError({ code: "SOL_CREDENTIAL_STORE_FAILED" })).toContain("No se modificó ningún perfil existente");
    expect(describeSolVaultError({ code: "SOL_CREDENTIAL_FIELD_TOO_LONG" })).toContain("demasiado largo");
  });

  it("tras un fallo ambiguo detecta un perfil nuevo ya guardado y no lo confunde con la secundaria existente", () => {
    const secondary = { id: "s1", credential_kind: "SECONDARY", profile_name: "Credencial secundaria" };
    const primary = { id: "p1", credential_kind: "PRIMARY_OWN_RUC", profile_name: "SOL Principal" };
    expect(findPersistedProfile([secondary], [secondary, primary], "PRIMARY_OWN_RUC", "sol principal")?.id).toBe("p1");
    expect(findPersistedProfile([secondary], [secondary], "PRIMARY_OWN_RUC", "SOL Principal")).toBeNull();
    expect(findPersistedProfile([secondary, primary], [secondary, primary], "PRIMARY_OWN_RUC", "")).toBeNull();
  });
});
