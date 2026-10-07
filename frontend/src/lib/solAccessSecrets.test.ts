// Barrido estatico del acceso SOL supervisado (Domain F): sin persistencia de secretos, sin acciones posteriores al
// menu SOL, sin desafio en la web, sin comunicacion directa con el agente ni SUNAT.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const FILES = [
  "src/lib/solAccess.ts",
  "src/lib/solAccessModel.ts",
  "src/lib/solAccessPermissions.ts",
  "src/lib/types/solAccess.ts",
  "src/app/(app)/clients/[id]/sol-access/page.tsx",
];
const sources = FILES.map((path) => ({ path, source: readFileSync(join(process.cwd(), path), "utf-8") }));
const code = (s: string) => s.split("\n").map((l) => l.replace(/^\s*\/\/.*$/, "")).join("\n");
const page = sources.find((s) => s.path.endsWith("page.tsx"))!.source;

describe("acceso SOL supervisado -- fuentes", () => {
  it("no usa almacenamiento del navegador ni consola", () => {
    for (const { path, source } of sources) {
      expect(source, path).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
      expect(code(source), path).not.toMatch(/console\./);
    }
  });

  it("no navega con secretos ni habla con SUNAT o el agente", () => {
    for (const { path, source } of sources) {
      expect(source, path).not.toMatch(/router\.(push|replace)\(|history\.pushState|window\.location/);
      expect(source, path).not.toMatch(/sunat\.gob|127\.0\.0\.1|localhost|\/agent\//i);
      expect(source, path).not.toMatch(/sol_username|sol_password|vault_root_key|credential_dek/);
    }
  });

  it("la contrasena de la boveda es type=password, se limpia y no se persiste", () => {
    expect(page).toMatch(/type="password"/);
    expect(page).toMatch(/setVaultPassword\(""\)/);
    expect(page).toContain('id="sol-access-vault-password"'); // etiqueta asociada (nombre accesible)
  });

  it("no hay botones posteriores al menu SOL ni desafio embebido", () => {
    for (const word of [/buz[oó]n/i, /mensajes/i, /declaraci/i, /pagar/i, /descargar/i, /<iframe/i, /<img/i, /captcha/i]) {
      expect(page, String(word)).not.toMatch(word);
    }
  });

  it("no hay inicio automatico: start solo desde el handler del boton", () => {
    expect(page.match(/createSolSession\(/g)?.length).toBe(1);
    expect(page).toMatch(/onClick=\{handleStart\}/);
  });

  it("el sondeo va solo al backend y se detiene en terminal", () => {
    expect(page).toContain("shouldPoll(");
    expect(page).toContain("fetchClientSolSession(");
  });
});
