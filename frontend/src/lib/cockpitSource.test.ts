// Barrido estatico del cockpit y del workspace (M1): una fila por cliente, sin acciones SUNAT falsas, sin almacenamiento
// del navegador, sin logica de negocio duplicada, escritura SOLO por el endpoint de estados manuales.
// (Entorno vitest = Node, sin render de componentes.)
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf-8");
const page = read("src/app/(app)/cockpit/page.tsx");
const workspacePage = read("src/app/(app)/cockpit/[clientId]/page.tsx");
const sections = read("src/components/cockpit/WorkspaceSections.tsx");
const controls = read("src/components/cockpit/OperationsControls.tsx");
const sidebar = read("src/components/layout/Sidebar.tsx");
const header = read("src/components/layout/Header.tsx");
const navigation = read("src/lib/navigation.ts");

const FAKE_SUNAT_ACTIONS = [
  /Consultar Buz[oó]n/i,
  /Abrir SIRE/i,
  /Descargar constancia/i,
  /Sincronizar CONTASIS/i,
  /Ficha RUC/i,
  /Declaraciones y Pagos/i,
  /Declara F[aá]cil/i,
  /Tr[aá]mites y Consultas/i,
  /sunat\.gob/i,
];

describe("cockpit page", () => {
  it("renderiza UNA fila por cliente (clave = client_id) y no por declaracion", () => {
    expect(page).toContain("key={row.client_id}");
    expect(page).not.toMatch(/declaration_id|\.id\}/);
  });

  it("la lista solo lee: ningun POST/PUT/PATCH/DELETE (los estados manuales se editan en el workspace)", () => {
    expect(page).not.toMatch(/apiClient\.(post|put|patch|delete)/);
    expect(page).not.toMatch(/updateOperations/);
  });

  it("no ofrece acciones SUNAT falsas ni usa almacenamiento del navegador", () => {
    for (const forbidden of [...FAKE_SUNAT_ACTIONS, /Abrir SOL/i, /localStorage|sessionStorage/]) {
      expect(page).not.toMatch(forbidden);
    }
  });

  it("los enlaces apuntan a rutas existentes", () => {
    const presentation = read("src/lib/cockpitPresentation.ts");
    expect(presentation).toContain('"/declarations/generate"');
    expect(presentation).toContain("/cockpit/${clientId}");
    expect(page).toContain("rowActions(row)");
  });

  it("muestra backend-computed: fase, SIRE y riesgo desde la fila, sin recalcular en React", () => {
    expect(page).toContain("row.monthly_phase");
    expect(page).toContain("row.sire_status");
    expect(page).toContain("row.overall_risk");
    expect(page).not.toMatch(/compute|computeRisk|new Date\(row\./);
  });

  it("tiene las 14 columnas (11 originales + Buzón, Contabilidad, Comunicación)", () => {
    for (const header of [
      "Cliente", "RUC", "Vence", "Fase", "Documentos", "SIRE", "Declaraciones", "Impuesto", "Honorarios",
      "Buzón", "Contabilidad", "Comunicación", "Riesgo", "Acción",
    ]) {
      expect(page).toContain(`"${header}"`);
    }
  });

  it("la tabla desplaza en su propia area (sin desbordar la pagina)", () => {
    expect(page).toContain('overflowX: "auto"');
  });
});

describe("workspace cliente/periodo", () => {
  it("el periodo y la pestana viven en la URL (refresh/atras/adelante) via router y search params", () => {
    expect(workspacePage).toContain('searchParams.get("period")');
    expect(workspacePage).toContain('searchParams.get("tab")');
    expect(workspacePage).toContain("router.push(workspaceUrl(");
    expect(workspacePage).toContain("<Suspense");
    expect(workspacePage).not.toMatch(/localStorage|sessionStorage/);
  });

  it("escribe SOLO por updateOperations (PUT de estados manuales) con expected_row_version", () => {
    expect(workspacePage).toContain("updateOperations(");
    expect(workspacePage).not.toMatch(/apiClient\.(post|put|patch|delete)/);
    expect(read("src/lib/cockpit.ts")).toContain("apiClient.put");
    expect(read("src/lib/cockpitWorkspace.ts")).toContain("expected_row_version");
  });

  it("maneja el 409 recargando y las pestañas piden los datos desde el backend (sin recalcular)", () => {
    expect(workspacePage).toContain("describeOperationsError(err)");
    // El mensaje se muestra DESPUES de recargar (load() limpia el error al empezar).
    const reloadAt = workspacePage.indexOf("if (info.reload) await load()");
    const messageAt = workspacePage.indexOf("setError(info.message)");
    expect(reloadAt).toBeGreaterThan(-1);
    expect(messageAt).toBeGreaterThan(reloadAt);
    expect(workspacePage).not.toMatch(/computeRisk|monthlyPhase\(|new Date\(summary\./);
  });

  it("los controles manuales solo se editan con can_update_operations (si no, solo lectura)", () => {
    expect(controls).toContain("canUpdate ? (");
    expect(controls).toContain("Solo lectura");
    expect(sections).toContain("canUpdate={workspace.can_update_operations}");
  });

  it("reutiliza los paneles existentes y la extraccion de honorarios", () => {
    expect(sections).toContain("DeclarationSectionLoader");
    expect(sections).toContain("ClientFeesPanel");
    const declarationSections = read("src/components/declarations/DeclarationSections.tsx");
    for (const panel of ["DocumentsPanel", "TaxAssessmentPanel", "TaxPaymentsPanel", "PresentationPanel", "ClosePanel"]) {
      expect(declarationSections).toContain(panel);
    }
    expect(read("src/app/(app)/clients/[id]/fees/page.tsx")).toContain("ClientFeesPanel");
    expect(read("src/app/(app)/declarations/[id]/page.tsx")).toContain("DeclarationSectionView");
  });

  it("incluye los avisos honestos: Buzón sin sincronizar y contabilidad en CONTASIS/DARWIN", () => {
    const workspace = read("src/lib/cockpitWorkspace.ts");
    expect(workspace).toContain("La sincronización automática del Buzón SUNAT no está activa");
    expect(workspace).toContain("El detalle contable permanece en CONTASIS/DARWIN.");
    expect(sections).toContain("ACCOUNTING_DETAIL_NOTICE");
    expect(sections).toContain("BUZON_SYNC_NOTICE");
  });

  it("no hay acciones muertas/falsas: unica accion SUNAT real = Acceso SOL supervisado", () => {
    for (const source of [workspacePage, sections, controls]) {
      for (const forbidden of FAKE_SUNAT_ACTIONS) expect(source).not.toMatch(forbidden);
    }
    expect(sections).toContain("Acceso SOL supervisado");
    expect(sections).toContain("/sol-access");
    expect(workspacePage).toContain("workspace?.sol_access");
  });

  it("los borradores de comunicacion se reutilizan sin ligarlos a un periodo", () => {
    expect(sections).toContain("/communications/new?client_id=");
    expect(sections).toContain("no están ligados a un periodo");
  });
});

describe("navegacion y layout", () => {
  it("la barra usa el modelo agrupado y marca el item activo con aria-current", () => {
    expect(sidebar).toContain("buildNavGroups(user)");
    expect(sidebar).toContain("aria-current");
    expect(navigation).toContain("Cockpit mensual");
    expect(navigation).toContain('"/dashboard"');
    expect(navigation).toContain("canViewCockpit(user)");
  });

  it("la navegacion movil es un cajon accesible (boton, aria-expanded, Escape, cierre al navegar)", () => {
    expect(header).toContain("aria-expanded={navOpen}");
    expect(header).toContain("aria-controls=\"app-navigation\"");
    expect(sidebar).toContain('event.key === "Escape"');
    expect(sidebar).toContain("[pathname]");
  });

  it("el layout deja a main encoger (min-width: 0) y el CSS define el cajon movil", () => {
    const css = read("src/app/globals.css");
    expect(css).toMatch(/\.app-main\s*{[^}]*min-width:\s*0/);
    expect(css).toContain("@media (max-width: 767px)");
    expect(css).toContain("position: fixed");
  });
});

describe("cajon movil: foco inmediato", () => {
  it("la visibilidad no se retrasa al abrir (si no, el foco al primer enlace falla)", () => {
    const css = read("src/app/globals.css");
    expect(css).not.toMatch(/transition:[^;]*visibility/);
  });
});
