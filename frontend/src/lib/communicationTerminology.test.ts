import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// Fase 3, Bloque 3.2 (ver ADR-0067): el sistema NUNCA puede confirmar que
// WhatsApp realmente abrio, entrego o fue leido -- por eso ciertas frases
// estan prohibidas en TODA la UI de comunicaciones, sin excepcion. Esta
// prueba escanea el codigo fuente real de las pantallas (no un DOM
// renderizado -- este repo no usa jsdom/testing-library, ver vitest.config)
// y falla si aparece cualquiera de esas frases, o si las frases honestas
// obligatorias no aparecen en ningun lado.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_SRC = path.resolve(__dirname, "..");

const SCANNED_FILES = [
  "app/(app)/communications/page.tsx",
  "app/(app)/communications/new/page.tsx",
  "app/(app)/communications/[id]/page.tsx",
  "components/ui/StatusSemaphore.tsx",
  "lib/communications.ts",
  "lib/communicationMutationError.ts",
  "lib/communicationDraftPayload.ts",
  "lib/communicationDraftOwnership.ts",
  "lib/types/communication.ts",
];

const FORBIDDEN_PHRASES = [
  "WhatsApp abierto",
  "Mensaje enviado",
  "Entregado",
  "Recibido",
  "Leído",
  "Confirmado por WhatsApp",
];

const REQUIRED_PHRASES = [
  "Se solicitó abrir WhatsApp",
  "Marcado como enviado por ti",
  "Pendiente de confirmación manual",
];

function readScannedSources(): { file: string; content: string }[] {
  return SCANNED_FILES.map((relative) => ({
    file: relative,
    content: readFileSync(path.join(FRONTEND_SRC, relative), "utf-8"),
  }));
}

describe("terminología de comunicaciones asistidas por WhatsApp", () => {
  const sources = readScannedSources();
  const combined = sources.map((s) => s.content).join("\n");

  it.each(FORBIDDEN_PHRASES)("nunca usa la frase prohibida '%s'", (phrase) => {
    for (const source of sources) {
      expect(
        source.content.includes(phrase),
        `'${phrase}' aparece en ${source.file}, pero el sistema nunca puede confirmar ese hecho`,
      ).toBe(false);
    }
  });

  it.each(REQUIRED_PHRASES)("usa la frase honesta obligatoria '%s' en algún lugar", (phrase) => {
    expect(combined.includes(phrase), `'${phrase}' no aparece en ningún archivo escaneado`).toBe(true);
  });
});
