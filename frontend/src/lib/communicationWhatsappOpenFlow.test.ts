import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// Fase 3, Bloque 3.2 (ver ADR-0067): verifica por inspeccion estatica del
// codigo fuente real (mismo criterio que communicationTerminology.test.ts --
// este repo no usa jsdom/testing-library) el contrato honesto de apertura de
// WhatsApp: el backend siempre registra la solicitud ANTES de intentar abrir
// la pestaña, el popup bloqueado nunca revierte el estado ni reintenta la
// mutacion automaticamente, y wa_url nunca toca almacenamiento persistente
// del navegador.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DETAIL_PAGE = path.resolve(
  __dirname,
  "../app/(app)/communications/[id]/page.tsx",
);
const COMMUNICATIONS_LIB_FILES = [
  path.resolve(__dirname, "communications.ts"),
  path.resolve(__dirname, "types/communication.ts"),
  DETAIL_PAGE,
];

function read(file: string): string {
  return readFileSync(file, "utf-8");
}

describe("flujo honesto de apertura de WhatsApp (detalle del borrador)", () => {
  const source = read(DETAIL_PAGE);

  it("llama a markOpenRequested antes de usar wa_url con window.open", () => {
    const markCallIndex = source.indexOf("await markOpenRequested(");
    const windowOpenIndex = source.indexOf("window.open(");
    expect(markCallIndex).toBeGreaterThan(-1);
    expect(windowOpenIndex).toBeGreaterThan(-1);
    expect(markCallIndex).toBeLessThan(windowOpenIndex);
  });

  it("window.open usa _blank y noopener,noreferrer, nunca reemplaza la pestaña actual", () => {
    expect(source).toContain('window.open(result.wa_url, "_blank", "noopener,noreferrer")');
    expect(source).not.toContain("window.location.href = result.wa_url");
  });

  it("cuando el popup es bloqueado, conserva el estado ya registrado y ofrece un enlace manual", () => {
    expect(source).toMatch(/if\s*\(\s*!opened\s*\)/);
    expect(source).toContain("setManualWaUrl(result.wa_url)");
    // El estado (`result.draft`, ya OPEN_REQUESTED) se fija ANTES de evaluar
    // si el popup fue bloqueado -- nunca se revierte ante el bloqueo.
    const setDraftIndex = source.indexOf("setDraft(result.draft)");
    const openedCheckIndex = source.indexOf("if (!opened)");
    expect(setDraftIndex).toBeGreaterThan(-1);
    expect(setDraftIndex).toBeLessThan(openedCheckIndex);
  });

  it("nunca reintenta automaticamente mark-open-requested (sin setTimeout/setInterval en el handler)", () => {
    expect(source).not.toMatch(/setTimeout|setInterval/);
  });

  it("wa_url nunca se escribe en localStorage ni sessionStorage en ningun archivo de comunicaciones", () => {
    // Se buscan llamadas reales a la API de almacenamiento (`.setItem(`,
    // `.getItem(`), no la sola mencion de la palabra -- el codigo documenta
    // deliberadamente esta ausencia en un comentario, lo cual es deseable.
    const storageApiCallPattern = /(localStorage|sessionStorage)\.(setItem|getItem)\(/;
    for (const file of COMMUNICATIONS_LIB_FILES) {
      const content = read(file);
      expect(content).not.toMatch(storageApiCallPattern);
    }
  });

  it("wa_url solo vive en useState del componente (nunca en un modulo/variable de nivel superior)", () => {
    expect(source).toContain("useState<string | null>(null)");
    expect(source).toContain("manualWaUrl");
  });
});
