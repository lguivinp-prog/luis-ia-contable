import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Regresión del 404 de «Revisar la propuesta de la Ficha RUC»: cada enlace interno `/clients/${id}/<segmento>[/${valor}]` que la UI
// construye debe tener su `page.tsx` en el App Router (carpeta `[id]/<segmento>[/[param]]`). Un enlace sin página real es un 404 seguro.
const ROOT = join(process.cwd(), "src");
const CLIENT_PAGES = join(ROOT, "app", "(app)", "clients", "[id]");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

// `/clients/${clientId}/segmento` o `/clients/${client.id}/segmento/${otro}`
// solo navegación (href / router.push / router.replace); las rutas de la API (`apiClient.get(...)`) no son páginas
const LINK =/(?:href=\{|push\(|replace\()`\/clients\/\$\{[^}]+\}\/([a-z][a-z-]*)(\/\$\{[^}]+\})?/g;

describe("los enlaces internos del cliente tienen una página real", () => {
  const sources = walk(ROOT).filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\./.test(f));
  const found = new Map<string, string>();
  for (const file of sources) {
    for (const match of readFileSync(file, "utf-8").matchAll(LINK)) {
      found.set(`${match[1]}${match[2] ? "/[param]" : ""}`, file);
    }
  }

  it("se detectan los enlaces conocidos (la prueba no es vacía)", () => {
    expect([...found.keys()]).toEqual(expect.arrayContaining(["sol-access", "ficha-ruc/[param]"]));
  });

  it.each([...found.keys()])("existe la página para /clients/{id}/%s", (key) => {
    const [segment, param] = key.split("/");
    const dir = param ? join(CLIENT_PAGES, segment, readdirSync(join(CLIENT_PAGES, segment)).find((n) => /^\[.+\]$/.test(n)) ?? "[missing]") : join(CLIENT_PAGES, segment);
    expect(existsSync(join(dir, "page.tsx")), `falta page.tsx para ${key}`).toBe(true);
  });

  it("la propuesta se abre en /clients/[id]/ficha-ruc/[snapshotId] y el Centro SUNAT enlaza exactamente esa ruta", () => {
    expect(existsSync(join(CLIENT_PAGES, "ficha-ruc", "[snapshotId]", "page.tsx"))).toBe(true);
    const center = readFileSync(join(CLIENT_PAGES, "sol-access", "page.tsx"), "utf-8");
    expect(center).toContain("`/clients/${clientId}/ficha-ruc/${pendingProposal.id}`");
    const info = readFileSync(join(ROOT, "components", "clients", "SunatInformation.tsx"), "utf-8");
    expect(info).toContain("`/clients/${clientId}/ficha-ruc/${overview.pending_snapshot_id}`");
  });
});
