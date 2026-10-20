import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  OFFICIAL_FILING_NOTICE,
  allowedDecisions,
  canReadSireControl,
  canWriteSireControl,
  decisionNeedsNotes,
  describeSireError,
  formatConceptValue,
  formatDifference,
  formatMoney,
  hasErrors,
  summaryInfo,
  validateManualForm,
} from "@/lib/sireControlModel";
import type { SireControl } from "@/lib/types/sireControl";

const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf-8");

describe("importes M4: cadenas decimales, sin float", () => {
  it("formatea con miles y 2 decimales operando solo sobre la cadena", () => {
    expect(formatMoney("11800.5")).toBe("S/ 11,800.50");
    expect(formatMoney("1234567.89", "USD")).toBe("US$ 1,234,567.89");
    expect(formatMoney("-45.10")).toBe("−S/ 45.10");
    expect(formatMoney("9999999999999.99")).toBe("S/ 9,999,999,999,999.99"); // sin perdida de precision (un float la perderia)
    expect(formatMoney(null)).toBe("—");
    expect(formatConceptValue("total_amount", null, "PEN")).toBe("desconocido"); // jamas 0.00
    expect(formatConceptValue("record_count", "120", "PEN")).toBe("120");
  });

  it("las diferencias conservan signo y se leen como DARWIN − SUNAT", () => {
    expect(formatDifference("-236.00", "total_amount")).toBe("−236.00");
    expect(formatDifference("4.50", "total_amount")).toBe("+4.50");
    expect(formatDifference("0.00", "total_amount")).toBe("0.00");
    expect(formatDifference("-2", "record_count")).toBe("−2");
    expect(formatDifference("3", "record_count")).toBe("+3");
    expect(formatDifference(null, "igv_amount")).toBe("—");
  });

  it("ningun modulo del control SIRE calcula importes con number/parseFloat", () => {
    for (const file of ["src/lib/sireControlModel.ts", "src/lib/sireControl.ts", "src/components/sire/SireComponentSection.tsx", "src/components/sire/SireSourceForm.tsx", "src/components/sire/SireImportWizard.tsx"]) {
      const code = read(file);
      expect(code).not.toMatch(/parseFloat|Number\(\s*(base|igv|total|amount)/i);
    }
  });
});

describe("validacion previa del ingreso manual (el backend la repite)", () => {
  const ok = { recordCount: "120", baseAmount: "10000.00", igvAmount: "1800.00", totalAmount: "11800.00", sourceLabel: "Reporte RVIE", sourceDate: "2026-02-05", notes: "" };

  it("acepta datos validos y deja vacio lo desconocido", () => {
    expect(hasErrors(validateManualForm(ok, "2026-02-10"))).toBe(false);
    expect(hasErrors(validateManualForm({ ...ok, baseAmount: "", igvAmount: "", totalAmount: "" }, "2026-02-10"))).toBe(false); // solo cantidad
  });

  it("rechaza importes mal formados, mas de 2 decimales, fechas futuras y fuentes sin identificar", () => {
    const e = validateManualForm({ ...ok, baseAmount: "1.234", igvAmount: "abc", totalAmount: "1,5", recordCount: "-1", sourceLabel: " ", sourceDate: "2026-03-01" }, "2026-02-10");
    expect(Object.keys(e).sort()).toEqual(["baseAmount", "igvAmount", "recordCount", "sourceDate", "sourceLabel", "totalAmount"]);
  });

  it("exige al menos un dato: nada se convierte en cero", () => {
    const e = validateManualForm({ ...ok, recordCount: "", baseAmount: "", igvAmount: "", totalAmount: "" }, "2026-02-10");
    expect(e.form).toContain("desconocido");
  });
});

describe("estados y reglas de revision", () => {
  it("cada codigo del backend tiene etiqueta propia; un codigo desconocido cae en «Sin expediente»", () => {
    for (const code of ["NO_CONTROL", "NO_SOURCES", "PARTIAL", "NOT_COMPARABLE", "MATCH", "DIFFERENCES", "MATCH_REVIEWED", "DIFFERENCES_REVIEWED", "NEEDS_REVIEW"]) {
      expect(summaryInfo(code).label.length).toBeGreaterThan(0);
    }
    expect(summaryInfo("OTRA_COSA").label).toBe("Sin expediente");
    expect(summaryInfo(undefined).label).toBe("Sin expediente");
    expect(new Set(["MATCH", "DIFFERENCES", "PARTIAL"].map((c) => summaryInfo(c).label)).size).toBe(3);
  });

  it("solo un control que cuadra puede verificarse; observar exige texto; sin fuentes no hay nada que revisar", () => {
    const control = (status: string) => ({ comparison: { status } }) as unknown as SireControl;
    expect(allowedDecisions(control("MATCH"))).toEqual(["APPROVED", "OBSERVED", "REQUIRES_REGULARIZATION"]);
    expect(allowedDecisions(control("DIFFERENCES"))).toEqual(["OBSERVED", "REQUIRES_REGULARIZATION"]);
    expect(allowedDecisions(control("PARTIAL"))).not.toContain("APPROVED");
    expect(allowedDecisions(control("NO_SOURCES"))).toEqual([]);
    expect(decisionNeedsNotes("APPROVED")).toBe(false);
    expect(decisionNeedsNotes("OBSERVED")).toBe(true);
  });

  it("los permisos de lectura y escritura son independientes", () => {
    const reader = { permissions: ["sire_control:read"] } as never;
    const writer = { permissions: ["sire_control:read", "sire_control:write"] } as never;
    expect([canReadSireControl(reader), canWriteSireControl(reader)]).toEqual([true, false]);
    expect([canReadSireControl(writer), canWriteSireControl(writer)]).toEqual([true, true]);
    expect(canReadSireControl(null)).toBe(false);
  });

  it("jamas se presenta un cuadre como constancia oficial", () => {
    expect(OFFICIAL_FILING_NOTICE).toContain("no es una constancia oficial");
    // un solo aviso en el contexto de los controles (panel) y otro en el reporte imprimible: no se repite por seccion
    expect(read("src/components/sire/SireComponentSection.tsx")).not.toContain("OFFICIAL_FILING_NOTICE}");
    for (const file of ["src/components/sire/SireControlPanel.tsx", "src/app/(app)/clients/[id]/sire-control/[controlId]/page.tsx"]) {
      expect(read(file)).toContain("OFFICIAL_FILING_NOTICE");
    }
  });
});

describe("errores del backend", () => {
  it("traduce codigos estables sin mostrar texto crudo", () => {
    expect(describeSireError({ code: "COMPARISON_CHANGED" }).message).toContain("Recarga");
    expect(describeSireError({ code: "SOURCE_CHANGED" }).message).toContain("Recarga");
    expect(describeSireError({ code: "FILE_TOO_LARGE" }).message).toContain("tamaño");
    expect(describeSireError({ code: "IMPORT_XLSX_FORMULA" }).message).toContain("fórmulas");
    expect(describeSireError({ code: "CODIGO_NUEVO", message: "Mensaje del servidor" }).message).toBe("Mensaje del servidor");
  });

  it("lista las filas invalidas de una importacion sin reproducir contenido de celdas", () => {
    const described = describeSireError({ code: "IMPORT_ROW_ERRORS", message: "x", errors: [{ row: 4, code: "IMPORT_TOTAL_ROW", message: "SECRETO" }, { row: 7, code: "IMPORT_NUMBER_INVALID" }] });
    expect(described.rows).toEqual(["Fila 4: fila de totales no comparable (exclúyela del archivo o confirma ignorar los totales)", "Fila 7: valor no numérico o con más de 2 decimales"]);
    expect(JSON.stringify(described)).not.toContain("SECRETO");
  });

  it("un 422 de validacion de FastAPI da un mensaje generico y no refleja valores", () => {
    expect(describeSireError([{ loc: ["body", "total_amount"], msg: "x", input: "VALOR" }]).message).toContain("Revisa");
    expect(describeSireError(null).message).toContain("No se pudo");
  });
});

describe("integracion en el workspace y el cockpit", () => {
  it("la pestaña SIRE monta el panel de control junto al acceso SUNAT M3 existente, sin reemplazar el estado juridico", () => {
    const sections = read("src/components/cockpit/WorkspaceSections.tsx");
    expect(sections).toContain("<SireControlPanel clientId={clientId} periodId={periodId} />");
    expect(sections).toContain("<SunatDestinationsPanel");
    expect(sections).toContain("Estado SIRE"); // el estado juridico de presentacion se conserva
  });

  it("el cockpit agrega UNA columna resumida por cliente y conserva la columna juridica SIRE", () => {
    const page = read("src/app/(app)/cockpit/page.tsx");
    expect(page).toContain('"SIRE", "Control SIRE"');
    expect(page).toContain("SIRE_STATUS_SEMAPHORE");
    expect(page).toContain("sire_control_rvie");
  });

  it("ninguna pieza del control SIRE habla con SUNAT, la boveda o el agente", () => {
    for (const file of ["src/lib/sireControl.ts", "src/components/sire/SireComponentSection.tsx", "src/components/sire/SireControlPanel.tsx", "src/components/sire/SireImportWizard.tsx", "src/components/sire/SireSourceForm.tsx"]) {
      expect(read(file)).not.toMatch(/sol-access|sol-vault|sunat\.gob|createSolSession|windows_agent/);
    }
  });
});
