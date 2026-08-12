// Barrido estatico de las fuentes frontend de la boveda SOL (Fase 5, Bloque
// 5.1). El entorno de pruebas es Node puro (ver vitest.config.ts,
// environment: "node", sin jsdom) -- no hay renderizado de componentes en
// esta base de codigo, asi que la ausencia de persistencia de secretos se
// verifica leyendo el codigo fuente real, mismo enfoque que
// `test_productive_sol_vault_modules_never_import_logging_or_call_print` en
// el backend (backend/tests/integration/test_sol_vault_secrets_and_redaction.py).
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SOL_VAULT_FRONTEND_FILES = [
  "src/lib/solVault.ts",
  "src/lib/solVaultErrors.ts",
  "src/lib/solVaultPermissions.ts",
  "src/lib/solVaultValidation.ts",
  "src/lib/types/solVault.ts",
  "src/app/(app)/settings/sol-vault/page.tsx",
  "src/app/(app)/clients/[id]/sol-credential/page.tsx",
];

function readSolVaultFile(relativePath: string): string {
  return readFileSync(join(process.cwd(), relativePath), "utf-8");
}

const ALL_SOURCES = SOL_VAULT_FRONTEND_FILES.map((path) => ({ path, source: readSolVaultFile(path) }));

describe("boveda SOL frontend -- ausencia de persistencia de secretos", () => {
  it("ningun archivo usa localStorage ni sessionStorage", () => {
    for (const { path, source } of ALL_SOURCES) {
      expect(source, `${path} no deberia usar localStorage`).not.toMatch(/localStorage/);
      expect(source, `${path} no deberia usar sessionStorage`).not.toMatch(/sessionStorage/);
    }
  });

  it("ningun archivo escribe secretos en la URL/history (router.push/replace, history.pushState, window.location)", () => {
    // La bandera aqui es la sola PRESENCIA de esas APIs junto a un nombre de
    // variable sensible en la misma linea -- ninguna pagina de este bloque
    // navega usando password/recovery_code como parte de la ruta o query.
    const secretIdentifiers = ["password", "recovery_code", "recoveryCode", "vaultRootKey", "credentialDek"];
    const navigationApiPattern = /router\.(push|replace)\(|history\.pushState\(|window\.location/;
    for (const { path, source } of ALL_SOURCES) {
      for (const line of source.split("\n")) {
        if (navigationApiPattern.test(line)) {
          for (const identifier of secretIdentifiers) {
            expect(
              line.toLowerCase().includes(identifier.toLowerCase()),
              `${path} parece navegar con un secreto en la linea: ${line}`,
            ).toBe(false);
          }
        }
      }
    }
  });

  it("nunca referencia material criptografico ni hashes que el backend jamas devuelve", () => {
    const forbiddenBackendOnlyFields = [
      "vault_root_key",
      "credential_dek",
      "wrapped_vault_root_key",
      "wrapped_data_key",
      "kdf_salt",
    ];
    for (const { path, source } of ALL_SOURCES) {
      for (const field of forbiddenBackendOnlyFields) {
        expect(source, `${path} no deberia referenciar ${field}`).not.toContain(field);
      }
    }
  });

  it("nunca ofrece un boton de ver/mostrar/copiar contrasena guardada", () => {
    const forbiddenRevealPatterns = [
      /ver\s+contrase/i,
      /mostrar\s+contrase/i,
      /copiar\s+contrase/i,
      /revealPassword/i,
      /showPassword/i,
    ];
    for (const { path, source } of ALL_SOURCES) {
      for (const pattern of forbiddenRevealPatterns) {
        expect(pattern.test(source), `${path} no deberia coincidir con ${pattern}`).toBe(false);
      }
    }
  });
});

describe("boveda SOL frontend -- textos obligatorios literales", () => {
  it("la pagina de configuracion incluye las advertencias obligatorias sobre bloqueo y recuperacion", () => {
    const settingsSource = readSolVaultFile("src/app/(app)/settings/sol-vault/page.tsx");
    expect(settingsSource).toContain("La bóveda se bloquea al reiniciar la aplicación.");
    expect(settingsSource).toContain("Guarda el código de recuperación fuera de esta computadora.");
  });

  it("la pagina de credencial SOL incluye las dos advertencias obligatorias exactas", () => {
    const credentialSource = readSolVaultFile("src/app/(app)/clients/[id]/sol-credential/page.tsx");
    expect(credentialSource).toContain(
      "Luis IA Contable nunca muestra nuevamente una contraseña SOL guardada.",
    );
    expect(credentialSource).toContain(
      "La credencial quedará pendiente de validación hasta comprobarla mediante un acceso supervisado",
    );
  });
});

describe("boveda SOL frontend -- enum real de credential_kind", () => {
  it("el tipo SolCredentialKind solo admite los 3 valores reales del backend", () => {
    const typesSource = readSolVaultFile("src/lib/types/solVault.ts");
    expect(typesSource).toContain("SECONDARY");
    expect(typesSource).toContain("PRIMARY_OWN_RUC");
    expect(typesSource).toContain("PRIMARY_EXCEPTIONAL");
  });

  it("ningun archivo reintroduce el valor incorrecto \"PRIMARY\" como credential_kind", () => {
    // \b no separa "PRIMARY" de "_OWN_RUC"/"_EXCEPTIONAL" porque "_" es un
    // caracter de palabra en JS -- por eso esta prueba nunca marca en falso
    // a PRIMARY_OWN_RUC/PRIMARY_EXCEPTIONAL, solo al valor suelto "PRIMARY".
    // Se ignoran los comentarios (`//...`) porque el propio comentario
    // documental de solVault.ts menciona la palabra "PRIMARY" al explicar
    // por que no existe ese valor -- eso no es una reintroduccion del bug.
    for (const { path, source } of ALL_SOURCES) {
      const codeOnly = source
        .split("\n")
        .map((line) => line.replace(/\/\/.*$/, ""))
        .join("\n");
      expect(/\bPRIMARY\b/.test(codeOnly), `${path} reintroduce el enum incorrecto "PRIMARY"`).toBe(false);
    }
  });
});

describe("boveda SOL frontend -- paginas protegidas por permiso", () => {
  it("la pagina de configuracion rechaza el render principal sin sol_vault:unlock", () => {
    const settingsSource = readSolVaultFile("src/app/(app)/settings/sol-vault/page.tsx");
    expect(settingsSource).toMatch(/if\s*\(!canUnlock\)/);
    expect(settingsSource).toContain("No tienes permiso para ver la bóveda de credenciales SOL.");
  });

  it("la pagina de credencial SOL rechaza el render principal sin sol_credential:read", () => {
    const credentialSource = readSolVaultFile("src/app/(app)/clients/[id]/sol-credential/page.tsx");
    expect(credentialSource).toMatch(/if\s*\(!canRead\)/);
    expect(credentialSource).toContain("No tienes permiso para ver la credencial SOL de este cliente.");
  });
});
