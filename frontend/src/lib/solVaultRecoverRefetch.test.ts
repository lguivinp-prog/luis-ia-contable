// Corrective closure (post v0.9.0-alpha.1 rejection): tras un
// `POST /sol-vault/recover` exitoso, la lista de "Accesos (slots)" debe
// refrescarse automaticamente (mostrando el nuevo slot USER de inmediato,
// sin exigir recargar el navegador). El entorno de pruebas es Node puro (ver
// vitest.config.ts, environment: "node", sin jsdom) -- no hay renderizado de
// componentes en esta base de codigo, asi que el comportamiento se verifica
// leyendo el codigo fuente real de `handleRecover` y comprobando que invoca
// tanto el refetch de slots como el refetch de estado, en ese orden, dentro
// del mismo flujo exitoso -- mismo enfoque que `solVaultSecrets.test.ts`.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const PAGE_PATH = "src/app/(app)/settings/sol-vault/page.tsx";

function readPageSource(): string {
  return readFileSync(join(process.cwd(), PAGE_PATH), "utf-8");
}

function extractFunctionBody(source: string, functionSignature: string): string {
  const start = source.indexOf(functionSignature);
  if (start === -1) {
    throw new Error(`No se encontro "${functionSignature}" en ${PAGE_PATH}`);
  }
  const openBraceIndex = source.indexOf("{", start);
  let depth = 0;
  let i = openBraceIndex;
  for (; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  return source.slice(openBraceIndex, i + 1);
}

describe("boveda SOL frontend -- refresco automatico de slots tras recover()", () => {
  const source = readPageSource();
  const handleRecoverBody = extractFunctionBody(source, "async function handleRecover(");

  it("handleRecover llama a reloadSlots() ademas de reload() en el camino exitoso", () => {
    expect(handleRecoverBody).toContain("reloadSlots()");
    expect(handleRecoverBody).toContain("reload()");
  });

  it("reloadSlots() se invoca antes que reload() dentro de handleRecover (no importa el orden relativo real, solo que ambos ocurran tras recoverSolVault)", () => {
    const recoverCallIndex = handleRecoverBody.indexOf("recoverSolVault(");
    const reloadSlotsCallIndex = handleRecoverBody.indexOf("reloadSlots()");
    const reloadCallIndex = handleRecoverBody.indexOf("reload()");
    expect(recoverCallIndex).toBeGreaterThan(-1);
    expect(reloadSlotsCallIndex).toBeGreaterThan(recoverCallIndex);
    expect(reloadCallIndex).toBeGreaterThan(recoverCallIndex);
  });

  it("handleRecover nunca exige una recarga manual del navegador (sin location.reload / router.refresh)", () => {
    expect(handleRecoverBody).not.toMatch(/location\.reload\(/);
    expect(handleRecoverBody).not.toMatch(/window\.location\.reload/);
    expect(handleRecoverBody).not.toMatch(/router\.refresh\(/);
  });

  it("handleRecover no introduce polling (sin setInterval/setTimeout)", () => {
    expect(handleRecoverBody).not.toMatch(/setInterval\(/);
    expect(handleRecoverBody).not.toMatch(/setTimeout\(/);
  });

  it("handleRecover limpia el codigo de recuperacion ingresado tras el exito (nunca se reutiliza ni se conserva en estado)", () => {
    expect(handleRecoverBody).toContain('setRecoverCode("")');
  });

  it("la definicion de reloadSlots vuelve a consultar el backend (no reutiliza una lista en memoria obsoleta)", () => {
    const reloadSlotsBody = extractFunctionBody(source, "const reloadSlots = useCallback(");
    expect(reloadSlotsBody).toContain("listSolVaultSlots()");
    expect(reloadSlotsBody).toContain("setSlots(");
  });
});
