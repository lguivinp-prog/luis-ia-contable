import { describe, expect, it } from "vitest";

import { isCommunicationDraftOwnedByAsistente } from "@/lib/communicationDraftOwnership";

const DRAFT = { created_by: "user-1" };

describe("isCommunicationDraftOwnedByAsistente", () => {
  it("siempre es true para admin, sin importar quien creo el borrador", () => {
    expect(isCommunicationDraftOwnedByAsistente({ role: "admin", id: "user-2" }, DRAFT)).toBe(true);
  });

  it("siempre es true para contador", () => {
    expect(isCommunicationDraftOwnedByAsistente({ role: "contador", id: "user-2" }, DRAFT)).toBe(true);
  });

  it("es true para asistente cuando el creador coincide", () => {
    expect(isCommunicationDraftOwnedByAsistente({ role: "asistente", id: "user-1" }, DRAFT)).toBe(true);
  });

  it("es false para asistente cuando el creador es otro usuario", () => {
    expect(isCommunicationDraftOwnedByAsistente({ role: "asistente", id: "user-2" }, DRAFT)).toBe(false);
  });

  it("es true cuando no hay usuario (estado de carga)", () => {
    expect(isCommunicationDraftOwnedByAsistente(null, DRAFT)).toBe(true);
  });
});
