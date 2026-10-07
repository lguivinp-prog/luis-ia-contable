import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/apiClient";
import {
  BUZON_SYNC_NOTICE,
  CONFLICT_CODE,
  WORKSPACE_TABS,
  buildNotesUpdate,
  buildStatusUpdate,
  describeOperationsError,
  historyValueLabel,
  isVersionConflict,
  lastUpdateLabel,
  parseWorkspaceTab,
  sireDeclarations,
  visibleWorkspaceTabs,
  workspaceUrl,
} from "@/lib/cockpitWorkspace";
import {
  ACCOUNTING_STATUS_SEMAPHORE,
  BUZON_STATUS_SEMAPHORE,
  COMMUNICATION_STATUS_SEMAPHORE,
  semaphoreOptions,
  workspaceHref,
} from "@/lib/cockpitPresentation";
import { fetchOperationsHistory, fetchWorkspace, updateOperations } from "@/lib/cockpit";
import type { User } from "@/lib/types/auth";
import type { OperationsState, WorkspaceDeclaration } from "@/lib/types/cockpit";
import { vi } from "vitest";

vi.mock("@/lib/apiClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/apiClient")>("@/lib/apiClient");
  return { ...actual, apiClient: { get: vi.fn().mockResolvedValue({}), put: vi.fn().mockResolvedValue({}) } };
});

const ops = (overrides: Partial<OperationsState> = {}): OperationsState => ({
  buzon_status: "NOT_REVIEWED",
  accounting_status: "NOT_STARTED",
  client_communication_status: "NOT_STARTED",
  notes: null,
  row_version: 0,
  updated_at: null,
  updated_by_name: null,
  ...overrides,
});

const user = (permissions: string[]) => ({ permissions }) as unknown as User;

describe("pestanas del workspace", () => {
  it("tiene las 10 secciones pedidas, en orden", () => {
    expect(WORKSPACE_TABS.map((t) => t.label)).toEqual([
      "Resumen", "Apertura / Buzón", "Documentos", "SIRE", "Declaraciones", "Contabilidad", "Pagos", "Honorarios",
      "Comunicación", "Historial",
    ]);
  });

  it("una pestana invalida o ausente cae en Resumen", () => {
    expect(parseWorkspaceTab("sire")).toBe("sire");
    expect(parseWorkspaceTab("inventada")).toBe("resumen");
    expect(parseWorkspaceTab(null)).toBe("resumen");
  });

  it("Honorarios solo aparece con professional_fee:read", () => {
    expect(visibleWorkspaceTabs(user(["dashboard:read"])).map((t) => t.key)).not.toContain("honorarios");
    expect(visibleWorkspaceTabs(user(["professional_fee:read"])).map((t) => t.key)).toContain("honorarios");
  });

  it("el periodo (y la pestana distinta de Resumen) viajan en la URL", () => {
    expect(workspaceUrl("c1", "p1", "resumen")).toBe("/cockpit/c1?period=p1");
    expect(workspaceUrl("c1", "p1", "documentos")).toBe("/cockpit/c1?period=p1&tab=documentos");
    expect(workspaceHref("c1", "p1")).toBe("/cockpit/c1?period=p1");
    expect(workspaceHref("c1", "p1", "sire")).toBe("/cockpit/c1?period=p1&tab=sire");
  });

  it("separa las declaraciones SIRE", () => {
    const d = (code: string) => ({ obligation_code: code }) as WorkspaceDeclaration;
    expect(sireDeclarations([d("621"), d("SIRE-RVIE"), d("PLAME"), d("SIRE-RCE")]).map((x) => x.obligation_code)).toEqual([
      "SIRE-RVIE",
      "SIRE-RCE",
    ]);
  });
});

describe("estados manuales", () => {
  it("las opciones coinciden exactamente con los enums del backend", () => {
    expect(semaphoreOptions(BUZON_STATUS_SEMAPHORE).map((o) => o.value)).toEqual([
      "NOT_REVIEWED", "REVIEWED_CLEAR", "REVIEWED_ACTION_REQUIRED", "NOT_APPLICABLE",
    ]);
    expect(semaphoreOptions(ACCOUNTING_STATUS_SEMAPHORE).map((o) => o.value)).toEqual([
      "NOT_STARTED", "IN_PROGRESS", "REVIEWED", "COMPLETED", "NOT_APPLICABLE",
    ]);
    expect(semaphoreOptions(COMMUNICATION_STATUS_SEMAPHORE).map((o) => o.value)).toEqual([
      "NOT_STARTED", "PREPARED", "SENT", "NOT_APPLICABLE",
    ]);
  });

  it("cada cambio envia UN campo con la version leida", () => {
    expect(buildStatusUpdate(ops({ row_version: 3 }), "buzon_status", "REVIEWED_CLEAR")).toEqual({
      expected_row_version: 3,
      buzon_status: "REVIEWED_CLEAR",
    });
    expect(buildStatusUpdate(ops(), "accounting_status", "COMPLETED")).toEqual({
      expected_row_version: 0,
      accounting_status: "COMPLETED",
    });
  });

  it("las notas se recortan y vacias se envian como null", () => {
    expect(buildNotesUpdate(ops({ row_version: 1 }), "  hola  ")).toEqual({ expected_row_version: 1, notes: "hola" });
    expect(buildNotesUpdate(ops({ row_version: 1 }), "   ")).toEqual({ expected_row_version: 1, notes: null });
  });

  it("muestra quien y cuando solo si ya existe una fila", () => {
    expect(lastUpdateLabel(ops())).toBeNull();
    expect(lastUpdateLabel(ops({ row_version: 2, updated_at: "2026-02-10T15:00:00Z", updated_by_name: "Ana" }))).toContain("Ana");
  });
});

describe("errores y concurrencia", () => {
  const conflict = new ApiError(409, { code: CONFLICT_CODE, current_row_version: 4 });

  it("un 409 de version se explica y fuerza la recarga (dato desactualizado)", () => {
    expect(isVersionConflict(conflict)).toBe(true);
    const info = describeOperationsError(conflict);
    expect(info.reload).toBe(true);
    expect(info.message).toMatch(/Otra persona modificó/);
  });

  it("otro 409 no es un conflicto de version", () => {
    expect(isVersionConflict(new ApiError(409, { code: "OTRO" }))).toBe(false);
    expect(isVersionConflict(new Error("x"))).toBe(false);
  });

  it("403, 404 y errores genericos tienen mensajes propios", () => {
    expect(describeOperationsError(new ApiError(403, "x"))).toEqual({
      message: "No tienes permiso para cambiar estos estados.",
      reload: false,
    });
    expect(describeOperationsError(new ApiError(404, "x")).reload).toBe(true);
    expect(describeOperationsError(new Error("red")).reload).toBe(false);
  });
});

describe("contrato de la API del workspace", () => {
  it("usa los endpoints del backend (lectura, PUT de operaciones e historial)", async () => {
    const { apiClient } = await import("@/lib/apiClient");
    await fetchWorkspace("c1", "p1");
    await updateOperations("c1", "p1", { expected_row_version: 0, buzon_status: "REVIEWED_CLEAR" });
    await fetchOperationsHistory("c1", "p1");
    expect(vi.mocked(apiClient.get).mock.calls.map((c) => c[0])).toContain("/cockpit/clients/c1/periods/p1");
    expect(vi.mocked(apiClient.get).mock.calls.map((c) => c[0])).toContain("/cockpit/clients/c1/periods/p1/history");
    expect(vi.mocked(apiClient.put).mock.calls[0]).toEqual([
      "/cockpit/clients/c1/periods/p1/operations",
      { expected_row_version: 0, buzon_status: "REVIEWED_CLEAR" },
    ]);
  });

  it("el aviso del Buzón dice la verdad: no hay sincronizacion automatica", () => {
    expect(BUZON_SYNC_NOTICE).toMatch(/no está activa/);
  });
});

describe("historial", () => {
  it("traduce los valores a las etiquetas de la interfaz y respeta nulos y notas", () => {
    expect(historyValueLabel("buzon_status", "REVIEWED_CLEAR")).toBe("Revisado sin novedades");
    expect(historyValueLabel("accounting_status", "IN_PROGRESS")).toBe("En proceso");
    expect(historyValueLabel("client_communication_status", "SENT")).toBe("Enviada");
    expect(historyValueLabel("notes", "texto libre")).toBe("texto libre");
    expect(historyValueLabel("notes", null)).toBe("—");
  });
});
