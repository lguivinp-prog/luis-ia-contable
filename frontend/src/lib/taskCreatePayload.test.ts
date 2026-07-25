import { describe, expect, it } from "vitest";

import { buildTaskCreatePayload } from "@/lib/taskCreatePayload";

const BASE_FORM = {
  title: "Revisar declaración",
  description: "",
  taskType: "GENERAL" as const,
  priority: "NORMAL" as const,
  dueDate: "2026-08-01",
  dueTime: "",
  clientId: "",
};

describe("buildTaskCreatePayload", () => {
  it("omite client_id por completo cuando se selecciona 'Sin cliente'", () => {
    const payload = buildTaskCreatePayload(BASE_FORM);
    expect("client_id" in payload).toBe(false);
  });

  it("incluye client_id cuando se selecciona un cliente válido", () => {
    const payload = buildTaskCreatePayload({ ...BASE_FORM, clientId: "c-123" });
    expect(payload.client_id).toBe("c-123");
  });

  it("recorta título y envía descripción vacía como null", () => {
    const payload = buildTaskCreatePayload({ ...BASE_FORM, title: "  Cobrar honorario  ", description: "   " });
    expect(payload.title).toBe("Cobrar honorario");
    expect(payload.description).toBeNull();
  });

  it("envía due_time como null cuando no se especifica hora", () => {
    const payload = buildTaskCreatePayload(BASE_FORM);
    expect(payload.due).toEqual({ due_date: "2026-08-01", due_time: null });
  });

  it("envía due_time cuando se especifica una hora", () => {
    const payload = buildTaskCreatePayload({ ...BASE_FORM, dueTime: "14:30" });
    expect(payload.due).toEqual({ due_date: "2026-08-01", due_time: "14:30" });
  });
});
