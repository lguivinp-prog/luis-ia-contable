import { describe, expect, it } from "vitest";

import { buildTaskSummaryCards } from "@/lib/taskDashboardLinks";
import type { TaskDashboardSummary } from "@/lib/types/task";

const SUMMARY: TaskDashboardSummary = {
  overdue_tasks: 3,
  due_today: 2,
  due_next_7_days: 5,
  urgent_tasks: 1,
  unassigned_tasks: 4,
  assigned_to_me: 6,
};

describe("buildTaskSummaryCards", () => {
  it("enlaza 'Vencidas' al filtro canónico time_bucket=OVERDUE", () => {
    const card = buildTaskSummaryCards(SUMMARY).find((c) => c.label === "Vencidas");
    expect(card?.href).toBe("/tasks?time_bucket=OVERDUE");
    expect(card?.value).toBe(3);
  });

  it("enlaza 'Vencen hoy' al filtro canónico time_bucket=TODAY", () => {
    const card = buildTaskSummaryCards(SUMMARY).find((c) => c.label === "Vencen hoy");
    expect(card?.href).toBe("/tasks?time_bucket=TODAY");
  });

  it("enlaza 'Próximos 7 días' al filtro canónico time_bucket=NEXT_7_DAYS", () => {
    const card = buildTaskSummaryCards(SUMMARY).find((c) => c.label === "Próximos 7 días");
    expect(card?.href).toBe("/tasks?time_bucket=NEXT_7_DAYS");
  });

  it("enlaza 'Sin responsable' a assigned_to=unassigned", () => {
    const card = buildTaskSummaryCards(SUMMARY).find((c) => c.label === "Sin responsable");
    expect(card?.href).toBe("/tasks?assigned_to=unassigned");
  });

  it("enlaza 'Asignadas a mí' a assigned_to=me", () => {
    const card = buildTaskSummaryCards(SUMMARY).find((c) => c.label === "Asignadas a mí");
    expect(card?.href).toBe("/tasks?assigned_to=me");
  });

  it("enlaza 'Urgentes' a priority=URGENT", () => {
    const card = buildTaskSummaryCards(SUMMARY).find((c) => c.label === "Urgentes");
    expect(card?.href).toBe("/tasks?priority=URGENT");
  });

  it("produce exactamente 6 tarjetas, sin doble conteo de dimensiones", () => {
    expect(buildTaskSummaryCards(SUMMARY)).toHaveLength(6);
  });
});
