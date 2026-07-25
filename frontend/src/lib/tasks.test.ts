import { describe, expect, it } from "vitest";

import { buildTaskQuery } from "@/lib/tasks";

describe("buildTaskQuery", () => {
  it("construye el query para time_bucket=OVERDUE", () => {
    expect(buildTaskQuery({ time_bucket: "OVERDUE" })).toBe("?time_bucket=OVERDUE");
  });

  it("construye el query para time_bucket=TODAY", () => {
    expect(buildTaskQuery({ time_bucket: "TODAY" })).toBe("?time_bucket=TODAY");
  });

  it("construye el query para time_bucket=NEXT_7_DAYS", () => {
    expect(buildTaskQuery({ time_bucket: "NEXT_7_DAYS" })).toBe("?time_bucket=NEXT_7_DAYS");
  });

  it("construye el query para assigned_to=me", () => {
    expect(buildTaskQuery({ assigned_to: "me" })).toBe("?assigned_to=me");
  });

  it("construye el query para assigned_to=unassigned", () => {
    expect(buildTaskQuery({ assigned_to: "unassigned" })).toBe("?assigned_to=unassigned");
  });

  it("construye el query para un assigned_to con UUID literal", () => {
    const uuid = "3441b255-13f5-441f-a0ba-48db13b41157";
    expect(buildTaskQuery({ assigned_to: uuid })).toBe(`?assigned_to=${uuid}`);
  });

  it("combina varios filtros a la vez", () => {
    const qs = buildTaskQuery({ time_bucket: "OVERDUE", assigned_to: "me", client_id: "c-1" });
    const params = new URLSearchParams(qs.slice(1));
    expect(params.get("time_bucket")).toBe("OVERDUE");
    expect(params.get("assigned_to")).toBe("me");
    expect(params.get("client_id")).toBe("c-1");
  });

  it("omite filtros undefined", () => {
    expect(buildTaskQuery({ status: undefined, priority: undefined })).toBe("");
  });

  it("omite filtros con cadena vacía", () => {
    expect(buildTaskQuery({ client_id: "" })).toBe("");
  });

  it("devuelve cadena vacía sin ningún filtro", () => {
    expect(buildTaskQuery({})).toBe("");
  });
});
