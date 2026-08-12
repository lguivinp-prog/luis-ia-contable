import { describe, expect, it } from "vitest";

import {
  isValidVaultPassword,
  VAULT_PASSWORD_MAX_LENGTH,
  VAULT_PASSWORD_MIN_LENGTH,
} from "@/lib/solVaultValidation";

describe("isValidVaultPassword", () => {
  it("rechaza una contrasena de 13 caracteres (por debajo del minimo)", () => {
    expect(isValidVaultPassword("a".repeat(VAULT_PASSWORD_MIN_LENGTH - 1))).toBe(false);
  });

  it("acepta exactamente el minimo de 14 caracteres", () => {
    expect(isValidVaultPassword("a".repeat(VAULT_PASSWORD_MIN_LENGTH))).toBe(true);
  });

  it("acepta exactamente el maximo de 256 caracteres", () => {
    expect(isValidVaultPassword("a".repeat(VAULT_PASSWORD_MAX_LENGTH))).toBe(true);
  });

  it("rechaza 257 caracteres (por encima del maximo)", () => {
    expect(isValidVaultPassword("a".repeat(VAULT_PASSWORD_MAX_LENGTH + 1))).toBe(false);
  });

  it("acepta una frase larga sin ningun simbolo, mayuscula ni numero", () => {
    // Deliberadamente distinta de la politica de login: no exige reglas
    // artificiales de simbolo/mayuscula/numero, solo longitud.
    const passphrase = "una frase larga y facil de recordar para la boveda";
    expect(passphrase.length).toBeGreaterThanOrEqual(VAULT_PASSWORD_MIN_LENGTH);
    expect(isValidVaultPassword(passphrase)).toBe(true);
  });

  it("acepta una contrasena que solo tiene numeros si cumple la longitud", () => {
    expect(isValidVaultPassword("12345678901234")).toBe(true);
  });

  it("rechaza la cadena vacia", () => {
    expect(isValidVaultPassword("")).toBe(false);
  });
});
