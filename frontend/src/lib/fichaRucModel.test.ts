import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/apiClient";
import {
  OVERRIDABLE_FIELDS,
  allChangedFields,
  buildSelection,
  canOverrideField,
  canRetryWithoutSecrets,
  changedEntries,
  checkOnboardingRuc,
  deriveOnboardingStep,
  describeFichaError,
  describeReviewStatus,
  formatDiffValue,
  isPendingReview,
  isValidRuc,
  onboardingResumeQuery,
} from "@/lib/fichaRucModel";
import {
  acceptFichaSnapshot,
  cancelOnboarding,
  completeOnboarding,
  fetchFichaSnapshot,
  listFichaSnapshots,
  rejectFichaSnapshot,
  startOnboarding,
} from "@/lib/fichaRuc";
import type { DiffEntry } from "@/lib/types/fichaRuc";

vi.mock("@/lib/apiClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/apiClient")>("@/lib/apiClient");
  return { ...actual, apiClient: { get: vi.fn().mockResolvedValue({}), post: vi.fn().mockResolvedValue({}) } };
});

const diff: DiffEntry[] = [
  { field: "business_name", label: "Razón social / nombres", current: "Alta asistida pendiente (20100070970)", proposed: "EMPRESA S.A.C.", changed: true },
  { field: "fiscal_address", label: "Domicilio fiscal", current: null, proposed: "AV. 1", changed: true },
  { field: "fiscal_address_condition", label: "Condición", current: "HABIDO", proposed: "HABIDO", changed: false },
  { field: "ruc_status", label: "Estado", current: "ACTIVO", proposed: "BAJA_PROVISIONAL", changed: true },
];

describe("diff de la Ficha", () => {
  it("solo los campos con cambio son aceptables", () => {
    expect(changedEntries(diff).map((d) => d.field)).toEqual(["business_name", "fiscal_address", "ruc_status"]);
    expect(allChangedFields(diff)).toEqual(["business_name", "fiscal_address", "ruc_status"]);
  });

  it("la seleccion descarta campos sin cambio o inexistentes y no acepta nada por defecto", () => {
    expect(buildSelection(diff, [], {}).accepted).toEqual([]);
    const sel = buildSelection(diff, ["business_name", "fiscal_address_condition", "monthly_fee"], {});
    expect(sel.accepted).toEqual(["business_name"]);
  });

  it("las correcciones solo valen para textos oficiales permitidos, aceptados y distintos de la propuesta", () => {
    const sel = buildSelection(diff, ["business_name", "ruc_status", "fiscal_address"], {
      business_name: "  EMPRESA CORREGIDA  ",
      ruc_status: "ACTIVO", // no corregible
      fiscal_address: "AV. 1", // igual a la propuesta: no es una correccion
      monthly_fee: "999", // ni siquiera es un campo del diff
    });
    expect(sel.overrides).toEqual({ business_name: "EMPRESA CORREGIDA" });
    expect([...OVERRIDABLE_FIELDS].sort()).toEqual(["business_name", "fiscal_address", "main_economic_activity", "taxpayer_type", "trade_name"]);
    expect(canOverrideField("ruc_status") || canOverrideField("activities_start_date")).toBe(false);
  });

  it("formatea valores sin inventar", () => {
    expect(formatDiffValue("business_name", null)).toBe("—");
    expect(formatDiffValue("activities_start_date", "2015-04-01")).toBe("01/04/2015");
    expect(formatDiffValue("ruc_status", "BAJA_PROVISIONAL")).toBe("Baja provisional");
    expect(formatDiffValue("person_type", "juridica")).toBe("Persona jurídica");
    expect(formatDiffValue("secondary_economic_activities", ["7020 - CONSULTORIA", "OTRA"])).toBe("7020 - CONSULTORIA; OTRA");
    expect(formatDiffValue("fiscal_address", "")).toBe("—");
  });

  it("estados de revision", () => {
    expect(isPendingReview({ review_status: "PENDING_REVIEW" })).toBe(true);
    for (const s of ["ACCEPTED", "REJECTED", "SUPERSEDED"]) expect(isPendingReview({ review_status: s })).toBe(false);
    expect(describeReviewStatus("PENDING_REVIEW")).toBe("Pendiente de revisión");
    expect(describeReviewStatus("???")).toBe("Estado desconocido");
  });
});

describe("errores saneados", () => {
  it("traduce codigos conocidos y nunca muestra el cuerpo crudo", () => {
    expect(describeFichaError({ code: "FICHA_SNAPSHOT_VERSION_CONFLICT" })).toMatch(/cambió mientras la revisabas/);
    expect(describeFichaError({ code: "ONBOARDING_CLIENT_ALREADY_EXISTS" })).toMatch(/Ya existe/);
    expect(describeFichaError("ONBOARDING_RUC_INVALID")).toBe("El RUC no es válido.");
    const generic = describeFichaError({ code: "CODIGO_DESCONOCIDO", password: "x" });
    expect(generic).toBe("No se pudo completar la operación. Inténtalo otra vez.");
    expect(generic).not.toContain("x");
  });
});

describe("RUC del alta asistida", () => {
  it("valida formato y digito verificador", () => {
    expect(isValidRuc("20100070970")).toBe(true);
    expect(isValidRuc("20100070971")).toBe(false);
    expect(isValidRuc("2010007097")).toBe(false);
    expect(isValidRuc("abc")).toBe(false);
    expect(isValidRuc("30100070970")).toBe(false); // prefijo inexistente
  });

  it("solo los prefijos 10 y 20 permiten el alta asistida (el tipo de persona no se infiere de otros)", () => {
    expect(checkOnboardingRuc("20100070970")).toBe("ok");
    expect(checkOnboardingRuc("20100070971")).toBe("invalid");
    const base = "15123456789".slice(0, 10);
    const factors = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
    const total = factors.reduce((s, f, i) => s + Number(base[i]) * f, 0);
    const r = 11 - (total % 11);
    const dv = r === 10 ? 0 : r === 11 ? 1 : r;
    expect(checkOnboardingRuc(base + dv)).toBe("unsupported_prefix");
  });
});

describe("pasos del alta asistida (estado autoritativo)", () => {
  const ob = (status: string) => ({ status });
  const ses = (status: string) => ({ status });

  it("sin alta => credenciales; completada/cancelada/en revision => su paso", () => {
    expect(deriveOnboardingStep(null, null)).toBe("credentials");
    expect(deriveOnboardingStep(ob("COMPLETED"), null)).toBe("done");
    expect(deriveOnboardingStep(ob("CANCELLED"), ses("HUMAN_CONTROL"))).toBe("cancelled");
    expect(deriveOnboardingStep(ob("AWAITING_REVIEW"), ses("HUMAN_CONTROL"))).toBe("review");
  });

  it("en curso: espera mientras la sesion este activa (incluido el control humano) y 'failed' si no hay sesion activa", () => {
    for (const s of ["CREATED", "WAITING_FOR_AGENT", "IN_PROGRESS", "WAITING_FOR_USER", "HUMAN_CONTROL"]) {
      expect(deriveOnboardingStep(ob("IN_PROGRESS"), ses(s))).toBe("waiting");
    }
    for (const s of ["FAILED", "CANCELLED", "EXPIRED", "SUCCEEDED"]) {
      expect(deriveOnboardingStep(ob("IN_PROGRESS"), ses(s))).toBe("failed");
    }
    expect(deriveOnboardingStep(ob("IN_PROGRESS"), null)).toBe("failed"); // cliente provisional sin sesion: reintentar
  });

  it("el reintento no vuelve a pedir secretos", () => {
    expect(canRetryWithoutSecrets("failed")).toBe(true);
    expect(canRetryWithoutSecrets("credentials")).toBe(false);
  });

  it("en la URL solo viaja el id del alta", () => {
    expect(onboardingResumeQuery("abc-123")).toBe("?onboarding=abc-123");
  });
});

describe("contrato de la API de la Ficha y del alta", () => {
  it("usa los endpoints del backend; el alta envia SOLO el RUC", async () => {
    const { apiClient } = await import("@/lib/apiClient");
    await listFichaSnapshots("c1");
    await fetchFichaSnapshot("c1", "s1");
    await acceptFichaSnapshot("c1", "s1", ["business_name"], 3);
    await rejectFichaSnapshot("c1", "s1", 3);
    await startOnboarding("20100070970");
    await completeOnboarding("o1", { accepted_fields: ["business_name"], overrides: {}, activate: false, expected_version: 2 });
    await cancelOnboarding("o1");
    const gets = vi.mocked(apiClient.get).mock.calls.map((c) => c[0]);
    expect(gets).toEqual(["/clients/c1/ficha-ruc/snapshots", "/clients/c1/ficha-ruc/snapshots/s1"]);
    const posts = Object.fromEntries(vi.mocked(apiClient.post).mock.calls.map((c) => [c[0], c[1]]));
    expect(posts["/client-onboardings"]).toEqual({ ruc: "20100070970" }); // nunca usuario/clave SOL ni contraseña de boveda
    expect(posts["/clients/c1/ficha-ruc/snapshots/s1/accept"]).toEqual({ accepted_fields: ["business_name"], expected_version: 3 });
    expect(posts["/clients/c1/ficha-ruc/snapshots/s1/reject"]).toEqual({ expected_version: 3 });
    expect(posts["/client-onboardings/o1/cancel"]).toBeUndefined();
    expect(Object.keys(posts)).toContain("/client-onboardings/o1/cancel");
  });

  it("un ApiError conserva el codigo para los mensajes saneados", () => {
    const err = new ApiError(409, { code: "ONBOARDING_VERSION_CONFLICT" });
    expect(describeFichaError(err.detail)).toMatch(/cambió mientras la revisabas/);
  });
});
