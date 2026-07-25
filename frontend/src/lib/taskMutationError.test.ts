import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/apiClient";
import { classifyTaskMutationError } from "@/lib/taskMutationError";

describe("classifyTaskMutationError", () => {
  it("clasifica un 409 TASK_VERSION_CONFLICT", () => {
    const err = new ApiError(409, { code: "TASK_VERSION_CONFLICT", message: "conflicto" });
    const result = classifyTaskMutationError(err);
    expect(result.kind).toBe("VERSION_CONFLICT");
    expect(result.message).toContain("modificada por otra persona");
  });

  it("clasifica un 409 TASK_NO_CHANGES", () => {
    const err = new ApiError(409, { code: "TASK_NO_CHANGES", message: "sin cambios" });
    const result = classifyTaskMutationError(err);
    expect(result.kind).toBe("NO_CHANGES");
    expect(result.message).toContain("ningún cambio real");
  });

  it("trata un 409 con otro código como OTHER", () => {
    const err = new ApiError(409, { code: "SOME_OTHER_CONFLICT", message: "otro" });
    const result = classifyTaskMutationError(err);
    expect(result.kind).toBe("OTHER");
  });

  it("trata un 403 como OTHER, usando el detalle textual", () => {
    const err = new ApiError(403, "No autorizado");
    const result = classifyTaskMutationError(err);
    expect(result.kind).toBe("OTHER");
    expect(result.message).toBe("No autorizado");
  });

  it("trata un error que no es ApiError como OTHER genérico", () => {
    const result = classifyTaskMutationError(new Error("boom"));
    expect(result.kind).toBe("OTHER");
    expect(result.message).toBe("Ocurrió un error inesperado.");
  });
});
