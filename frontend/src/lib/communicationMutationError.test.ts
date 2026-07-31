import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/apiClient";
import { classifyCommunicationMutationError } from "@/lib/communicationMutationError";

describe("classifyCommunicationMutationError", () => {
  it("clasifica un 409 VERSION_CONFLICT", () => {
    const err = new ApiError(409, { code: "VERSION_CONFLICT", message: "conflicto" });
    const result = classifyCommunicationMutationError(err);
    expect(result.kind).toBe("VERSION_CONFLICT");
    expect(result.message).toContain("modificado por otra persona");
  });

  it("clasifica un 409 INVALID_COMMUNICATION_TRANSITION", () => {
    const err = new ApiError(409, { code: "INVALID_COMMUNICATION_TRANSITION", message: "invalido" });
    const result = classifyCommunicationMutationError(err);
    expect(result.kind).toBe("INVALID_TRANSITION");
    expect(result.message).toContain("ya no es válida");
  });

  it("mapea DRAFT_NOT_OWNED a un mensaje conocido con kind OTHER", () => {
    const err = new ApiError(403, { code: "DRAFT_NOT_OWNED", message: "x" });
    const result = classifyCommunicationMutationError(err);
    expect(result.kind).toBe("OTHER");
    expect(result.message).toBe("Este borrador no te pertenece.");
  });

  it("mapea WHATSAPP_NUMBER_REQUIRED a un mensaje conocido", () => {
    const err = new ApiError(422, { code: "WHATSAPP_NUMBER_REQUIRED" });
    const result = classifyCommunicationMutationError(err);
    expect(result.message).toBe("El cliente no tiene un número de WhatsApp registrado.");
  });

  it("mapea INVALID_WHATSAPP_NUMBER a un mensaje conocido", () => {
    const err = new ApiError(422, { code: "INVALID_WHATSAPP_NUMBER" });
    const result = classifyCommunicationMutationError(err);
    expect(result.message).toContain("formato internacional");
  });

  it("mapea SOURCE_CLIENT_MISMATCH a un mensaje conocido", () => {
    const err = new ApiError(422, { code: "SOURCE_CLIENT_MISMATCH" });
    const result = classifyCommunicationMutationError(err);
    expect(result.message).toContain("no pertenece a este cliente");
  });

  it("mapea UNKNOWN_TEMPLATE_VARIABLE a un mensaje conocido", () => {
    const err = new ApiError(422, { code: "UNKNOWN_TEMPLATE_VARIABLE" });
    const result = classifyCommunicationMutationError(err);
    expect(result.kind).toBe("OTHER");
    expect(result.message).toContain("plantilla");
  });

  it("usa el detalle textual para un codigo desconocido", () => {
    const err = new ApiError(400, { code: "ALGO_NUEVO", message: "detalle" });
    const result = classifyCommunicationMutationError(err);
    expect(result.kind).toBe("OTHER");
  });

  it("usa el detalle textual cuando detail es un string", () => {
    const err = new ApiError(403, "No autorizado");
    const result = classifyCommunicationMutationError(err);
    expect(result.message).toBe("No autorizado");
  });

  it("trata un error que no es ApiError como OTHER genérico", () => {
    const result = classifyCommunicationMutationError(new Error("boom"));
    expect(result.kind).toBe("OTHER");
    expect(result.message).toBe("Ocurrió un error inesperado.");
  });
});
