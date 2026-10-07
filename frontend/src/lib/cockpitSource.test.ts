// Barrido estatico del cockpit (M1, Domain B): una fila por cliente, sin acciones SUNAT falsas, sin escritura, sin
// almacenamiento del navegador, sin logica de negocio duplicada. (Entorno vitest = Node, sin render de componentes.)
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf-8");
const page = read("src/app/(app)/cockpit/page.tsx");
const sidebar = read("src/components/layout/Sidebar.tsx");

describe("cockpit page", () => {
  it("renderiza UNA fila por cliente (clave = client_id) y no por declaracion", () => {
    expect(page).toContain("key={row.client_id}");
    expect(page).not.toMatch(/declaration_id|\.id\}/);
  });

  it("solo lee: ningun POST/PUT/PATCH/DELETE ni escritura de estado", () => {
    expect(page).not.toMatch(/apiClient\.(post|put|patch|delete)/);
    expect(read("src/lib/cockpit.ts")).not.toMatch(/\.(post|put|patch|delete)\(/);
  });

  it("no ofrece acciones SUNAT/SOL falsas ni usa almacenamiento del navegador", () => {
    for (const forbidden of [/Abrir SOL/i, /Buz[oó]n/i, /Ficha RUC/i, /localStorage|sessionStorage/, /sunat\.gob/i]) {
      expect(page).not.toMatch(forbidden);
    }
  });

  it("los enlaces apuntan a rutas existentes", () => {
    expect(read("src/lib/cockpitPresentation.ts")).toContain('"/declarations/generate"');
    expect(page).toContain("rowActions(row)");
  });

  it("muestra backend-computed: fase, SIRE y riesgo desde la fila, sin recalcular en React", () => {
    expect(page).toContain("row.monthly_phase");
    expect(page).toContain("row.sire_status");
    expect(page).toContain("row.overall_risk");
    expect(page).not.toMatch(/compute|computeRisk|new Date\(row\./);
  });

  it("tiene las 11 columnas pedidas", () => {
    for (const header of ["Cliente", "RUC", "Vence", "Fase", "Documentos", "SIRE", "Declaraciones", "Impuesto", "Honorarios", "Riesgo", "Acción"]) {
      expect(page).toContain(`"${header}"`);
    }
  });
});

describe("navegacion", () => {
  it("agrega 'Cockpit mensual' como item operativo sin eliminar el tablero existente", () => {
    expect(sidebar).toContain("Cockpit mensual");
    expect(sidebar).toContain('"/dashboard"');
    expect(sidebar).toContain("canViewCockpit(user)");
  });
});
