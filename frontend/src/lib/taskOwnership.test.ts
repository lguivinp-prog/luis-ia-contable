import { describe, expect, it } from "vitest";

import { isTaskOwnedByAsistente } from "@/lib/taskOwnership";

const ASISTENTE = { role: "asistente", id: "user-1" };
const ADMIN = { role: "admin", id: "user-2" };
const CONTADOR = { role: "contador", id: "user-3" };

describe("isTaskOwnedByAsistente", () => {
  it("devuelve true para el asistente cuando la tarea le está asignada", () => {
    expect(isTaskOwnedByAsistente(ASISTENTE, { assigned_to: "user-1" })).toBe(true);
  });

  it("devuelve false para el asistente cuando la tarea está asignada a otro usuario", () => {
    expect(isTaskOwnedByAsistente(ASISTENTE, { assigned_to: "user-99" })).toBe(false);
  });

  it("devuelve false para el asistente cuando la tarea no tiene responsable", () => {
    expect(isTaskOwnedByAsistente(ASISTENTE, { assigned_to: null })).toBe(false);
  });

  it("devuelve true para admin sin importar el responsable -- sin restricción por fila", () => {
    expect(isTaskOwnedByAsistente(ADMIN, { assigned_to: "user-99" })).toBe(true);
    expect(isTaskOwnedByAsistente(ADMIN, { assigned_to: null })).toBe(true);
  });

  it("devuelve true para contador sin importar el responsable -- sin restricción por fila", () => {
    expect(isTaskOwnedByAsistente(CONTADOR, { assigned_to: "user-99" })).toBe(true);
  });

  it("devuelve true para un usuario null (no bloquea, el gateo real es por permiso)", () => {
    expect(isTaskOwnedByAsistente(null, { assigned_to: "user-99" })).toBe(true);
  });
});
