import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { acceptanceStatusLabel, canConfirmAcceptance, currentFailures, describeStartError, DESTINATION_LABEL, proofHeadline, describeDestinationStates, destinationUiState, describeDiagnostic } from "@/lib/solAccessModel";
import { canManageAcceptance } from "@/lib/solAccessPermissions";
import type { SolAccessSessionOut } from "@/lib/types/solAccess";

type S = Pick<SolAccessSessionOut, "status" | "destination" | "diagnostic_code" | "is_terminal">;
const session = (status: string, destination: string | null, extra: Partial<S> = {}): S => ({
  status,
  destination,
  diagnostic_code: null,
  is_terminal: ["SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "PARTIAL_FAILURE"].includes(status),
  ...extra,
});
const enabled = { id: "SIRE" as const, enabled: true };
const disabled = { id: "SIRE" as const, enabled: false };

describe("estado de cada destino en la UI (M3)", () => {
  it("un destino NO validado jamás ofrece acción, sin importar la sesión", () => {
    for (const s of [null, session("HUMAN_CONTROL", "SIRE"), session("SUCCEEDED", "SIRE")]) {
      const state = destinationUiState(disabled, s);
      expect(state.key).toBe("NOT_VALIDATED");
      expect(state.canOpen).toBe(false);
      expect(state.hint).toContain("Abrir Menú SOL");
    }
  });

  it("distingue disponible, en curso, intervención humana, navegador entregado, finalizado, fallido y cancelado", () => {
    expect(destinationUiState(enabled, null)).toMatchObject({ key: "AVAILABLE", canOpen: true });
    expect(destinationUiState(enabled, session("IN_PROGRESS", "SIRE"))).toMatchObject({ key: "IN_PROGRESS", canOpen: false });
    expect(destinationUiState(enabled, session("WAITING_FOR_AGENT", "SIRE"))).toMatchObject({ key: "IN_PROGRESS", canOpen: false });
    expect(destinationUiState(enabled, session("WAITING_FOR_USER", "SIRE"))).toMatchObject({ key: "NEEDS_HUMAN", canOpen: false });
    expect(destinationUiState(enabled, session("HUMAN_CONTROL", "SIRE"))).toMatchObject({ key: "HANDED_OVER", canOpen: false });
    expect(destinationUiState(enabled, session("SUCCEEDED", "SIRE"))).toMatchObject({ key: "FINISHED", canOpen: true });
    expect(destinationUiState(enabled, session("FAILED", "SIRE"))).toMatchObject({ key: "FAILED", canOpen: true });
    expect(destinationUiState(enabled, session("CANCELLED", "SIRE"))).toMatchObject({ key: "CANCELLED", canOpen: true });
  });

  it("con otra sesión activa no se duplica la autenticación ni se muestra el estado de otro destino", () => {
    const other = session("HUMAN_CONTROL", "MENU_SOL");
    expect(destinationUiState(enabled, other)).toMatchObject({ key: "AVAILABLE", canOpen: false });
    expect(destinationUiState(enabled, session("SUCCEEDED", "MENU_SOL"))).toMatchObject({ key: "AVAILABLE", canOpen: true });
  });

  it("«no autorizado» solo se afirma con un código dedicado; la entrada ausente del menú se informa como fallo", () => {
    expect(destinationUiState(enabled, session("FAILED", "SIRE", { diagnostic_code: "DESTINATION_ENTRY_NOT_FOUND" })).key).toBe("FAILED");
    expect(destinationUiState(enabled, session("FAILED", "SIRE", { diagnostic_code: "DESTINATION_NOT_AUTHORIZED" })).key).toBe("NOT_AUTHORIZED");
    expect(describeDiagnostic("DESTINATION_ENTRY_NOT_FOUND")?.message).toContain("no puede distinguirlo");
  });

  it("el mensaje de un destino no validado distingue «falta la ruta», «solo apertura» y «sin evidencia» y nunca ofrece acción", () => {
    const none = destinationUiState({ id: "SIRE", enabled: false }, null);
    const arrival = destinationUiState({ id: "SIRE_RVIE", enabled: false, evidence: "ARRIVAL_ONLY" }, null);
    const openOnly = destinationUiState({ id: "SIRE_GENERACION_REGISTROS", enabled: false, evidence: "NONE", open_only: true }, null);
    for (const s of [none, arrival, openOnly]) expect(s).toMatchObject({ key: "NOT_VALIDATED", canOpen: false });
    expect(arrival.hint).toContain("falta confirmar cómo llegar");
    expect(openOnly.hint).toContain("apertura supervisada");
    expect(openOnly.hint).toContain("siempre queda en tus manos");
    expect(new Set([none.hint, arrival.hint, openOnly.hint]).size).toBe(3);
    const recon = destinationUiState({ id: "SIRE_RVIE", enabled: false, evidence: "RECON_COMPLETE" }, null);
    expect(recon).toMatchObject({ key: "NOT_VALIDATED", canOpen: false }); // contrato completo con evidencia real, aun sin aceptacion en vivo: NO se ofrece accion
    expect(recon.hint).toContain("falta aceptar la apertura automática en vivo");
    expect(describeDestinationStates({ recognized: "RECON_COMPLETE", enabled: false, permitted: "UNKNOWN", completed: false })).toContain("falta la aceptación en vivo");
  });

  it("los nueve destinos conocidos tienen etiqueta y los diagnósticos nuevos tienen mensaje", () => {
    expect(Object.keys(DESTINATION_LABEL)).toHaveLength(9);
    for (const code of ["DESTINATION_NOT_VALIDATED", "DESTINATION_ENTRY_NOT_FOUND", "DESTINATION_MENU_STRUCTURE_MISMATCH", "DESTINATION_IDENTITY_MISMATCH", "DESTINATION_FRAME_NOT_VALIDATED", "DESTINATION_PAGE_NOT_VALIDATED"]) {
      expect(describeDiagnostic(code)?.message, code).toBeTruthy();
    }
  });
});

describe("integración al workspace mensual y al Centro SUNAT", () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf-8");

  it("el panel de accesos vive en el Resumen y en SIRE del workspace y propaga el periodo como contexto", () => {
    const sections = read("src/components/cockpit/WorkspaceSections.tsx");
    expect(sections.match(/<SunatDestinationsPanel/g)?.length).toBe(2);
    expect(sections).toContain("originTaxPeriodId={periodId || undefined}");
  });

  it("el panel nunca ofrece acción para un destino no habilitado y solo envía el identificador del catálogo", () => {
    const panel = read("src/components/sunat/SunatDestinationsPanel.tsx");
    expect(panel).toContain('{info.enabled && state.key !== "NEEDS_PROOF" && (');
    expect(panel).toContain("state.canOpen");
    expect(panel).not.toMatch(/https?:\/\//);
    const api = read("src/lib/solAccess.ts");
    expect(api).toContain("origin_tax_period_id");
    expect(api).not.toMatch(/\burl\b\s*:/);
  });

  it("abrir un destino no escribe estados de cumplimiento (el panel no importa la API de operaciones ni de declaraciones)", () => {
    const panel = read("src/components/sunat/SunatDestinationsPanel.tsx");
    expect(panel).not.toMatch(/updateOperations|declarations|buzon_status|sire_status/);
  });

  it("el Centro SUNAT informa los destinos no validados y sigue ofreciendo «Abrir Menú SOL»", () => {
    const center = read("src/app/(app)/clients/[id]/sol-access/page.tsx");
    expect(center).toContain('data-testid="unvalidated-destinations"');
    expect(center).toContain("Abrir {DESTINATION_LABEL[destination]}");
  });
});

describe("correccion de alcance: sin duplicar accesos ni automatizar operaciones", () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf-8");

  it("el panel no ofrece tarjeta a alias ni a destinos contenidos y explica la equivalencia en la tarjeta real", () => {
    const panel = read("src/components/sunat/SunatDestinationsPanel.tsx");
    expect(panel).toContain("info.offered !== false");
    expect(panel).toMatch(/MENU_SOL: "Incluye «Trámites y Consultas»/);
    expect(panel).toMatch(/DECLARACIONES_Y_PAGOS: "Incluye «Declara Fácil»/);
  });

  it("la UI nunca envia URL, selector ni una operacion tributaria: solo el identificador del destino", () => {
    const api = read("src/lib/solAccess.ts");
    expect(api).not.toMatch(/upload|generate|submit|declare|pay|rectif/i);
    const panel = read("src/components/sunat/SunatDestinationsPanel.tsx");
    expect(panel).not.toMatch(/upload|generar registros|presentar|rectific/i);
  });
});

describe("perfiles de credencial (M3): seleccion explicita y cuatro estados distintos", () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf-8");
  const A = { id: "a", status: "ACTIVE" };
  const B = { id: "b", status: "NEEDS_VALIDATION" };
  const C = { id: "c", status: "INVALID" };

  it("con un solo perfil se usa ese (compatibilidad M2); con varios NO hay preseleccion", async () => {
    const { resolveSelectedProfile, selectionRequiredFor, usableProfiles } = await import("@/lib/solAccessModel");
    expect(resolveSelectedProfile([A], null)).toBe(A);
    expect(selectionRequiredFor([A], null)).toBe(false);
    expect(resolveSelectedProfile([A, B], null)).toBeNull();
    expect(selectionRequiredFor([A, B], null)).toBe(true);
    expect(resolveSelectedProfile([A, B], "b")).toBe(B);
    expect(selectionRequiredFor([A, B], "zzz")).toBe(true); // un id desconocido no cuenta como eleccion
    expect(usableProfiles([A, B, C]).map((p) => p.id)).toEqual(["a", "b"]); // un perfil INVALID nunca se ofrece ni se sustituye
  });

  it("el preflight pide elegir credencial cuando hay varios perfiles y no se eligio ninguno", async () => {
    const { evaluatePreflight } = await import("@/lib/solAccessModel");
    const vault = { initialized: true, status: "ACTIVE", unlocked: true } as never;
    const agents = { broker_enabled: true, agents: [{ available: true }] } as never;
    const out = evaluatePreflight({ canStart: true, credential: null, vault, agents, session: null, selectionRequired: true });
    expect(out.canStart).toBe(false);
    expect(out.checks.find((c) => c.id === "credential")?.detail).toContain("varios perfiles");
  });

  it("describeDestinationStates nunca mezcla reconocido, habilitado, permitido y completado", async () => {
    const { describeDestinationStates } = await import("@/lib/solAccessModel");
    const text = describeDestinationStates({ recognized: "ARRIVAL_ONLY", enabled: false, permitted: "UNKNOWN", completed: false });
    expect(text).toContain("Reconocido: solo la llegada reconocida");
    expect(text).toContain("Habilitado en Luis IA: no");
    expect(text).toContain("Permitido para este perfil: sin observar con este perfil");
    expect(text).toContain("Acceso completado: no");
    const done = describeDestinationStates({ recognized: "COMPLETE", enabled: true, permitted: "OBSERVED_ALLOWED", completed: true });
    expect(done).toContain("observado: el portal lo permitió");
    expect(describeDestinationStates({ recognized: "COMPLETE", enabled: true, permitted: "OBSERVED_NOT_IN_MENU", completed: false })).toContain("no aparece en el menú de este perfil");
  });

  it("el selector no preselecciona, no ofrece perfiles no utilizables y la UI nunca envia credenciales a la sesion", () => {
    const picker = read("src/components/sunat/CredentialProfilePicker.tsx");
    expect(picker).toContain("profiles.length <= 1");
    expect(picker).toContain("disabled={!usable.has(p.id)}");
    expect(picker).not.toMatch(/defaultChecked|useEffect/); // sin preseleccion
    expect(picker).toContain("no probará otra por su cuenta");
    const api = read("src/lib/solAccess.ts");
    expect(api).toContain("body.credential_id = options.credentialId");
    expect(api).not.toMatch(/sol_password|sol_username|password/);
    for (const file of ["src/components/sunat/SunatDestinationsPanel.tsx", "src/app/(app)/clients/[id]/sol-access/page.tsx"]) {
      const source = read(file);
      expect(source).toContain("credentialId: selectedProfile?.id");
      expect(source).toContain("CredentialProfilePicker");
    }
  });
});

describe("aceptacion real controlada (M3)", () => {
  it("el historial distingue autorizacion, consumo, fallo, llegada real y confirmacion humana", () => {
    expect(acceptanceStatusLabel({ phase: "AUTHORIZED" })).toContain("sin usar");
    expect(acceptanceStatusLabel({ phase: "CONSUMED_RUNNING" })).toContain("en curso");
    expect(acceptanceStatusLabel({ phase: "SESSION_FAILED" })).toContain("falló");
    expect(acceptanceStatusLabel({ phase: "ARRIVAL_AWAITING_CONFIRMATION" })).toContain("falta tu confirmación");
    expect(acceptanceStatusLabel({ phase: "CONFIRMED_PASS" })).toContain("correcta");
    expect(acceptanceStatusLabel({ phase: "CONFIRMED_FAIL", result_source: "SYSTEM" })).toContain("sistema");
    expect(acceptanceStatusLabel({ phase: "CONFIRMED_FAIL", result_source: "OWNER" })).toContain("no coincide");
    expect(acceptanceStatusLabel({ phase: "EXPIRED" })).toBe("vencida");
  });

  it("solo una llegada validada y entregada permite confirmar; una sesion fallida jamas ofrece «Correcta»", () => {
    expect(canConfirmAcceptance({ phase: "ARRIVAL_AWAITING_CONFIRMATION" })).toBe(true);
    for (const phase of ["AUTHORIZED", "CONSUMED_RUNNING", "SESSION_FAILED", "CONFIRMED_PASS", "CONFIRMED_FAIL", "REVOKED", "EXPIRED"]) {
      expect(canConfirmAcceptance({ phase })).toBe(false);
    }
  });

  it("solo el permiso de propietario muestra la aceptacion; un contador no la ve", () => {
    const owner = { permissions: ["sol_acceptance:manage"] } as never;
    const accountant = { permissions: ["sol_session:create", "sol_credential:read"] } as never;
    expect(canManageAcceptance(owner)).toBe(true);
    expect(canManageAcceptance(accountant)).toBe(false);
    expect(canManageAcceptance(null)).toBe(false);
  });
});


describe("destinos aceptados: prueba por perfil, jerarquía de estados y vista vigente (M3)", () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf-8");
  const rvie = { id: "SIRE_RVIE" as const, enabled: true, evidence: "LIVE_ACCEPTED", requires_profile_proof: true };
  const m2 = { id: "MENU_SOL" as const, enabled: true, requires_profile_proof: false };

  it("la estructura validada NO se extrapola: un perfil sin prueba propia ve «Requiere prueba» y ninguna acción; el probado ve «Disponible»", () => {
    const unproven = destinationUiState(rvie, null, { available: false, proven: false });
    expect(unproven).toMatchObject({ key: "NEEDS_PROOF", canOpen: false });
    expect(unproven.label).not.toBe("No validado");
    expect(destinationUiState(rvie, null, { available: true, proven: true })).toMatchObject({ key: "AVAILABLE", canOpen: true });
    expect(destinationUiState(m2, null, { available: true })).toMatchObject({ key: "AVAILABLE", canOpen: true }); // M2 conserva su regla
    expect(destinationUiState(rvie, null, null)).toMatchObject({ key: "AVAILABLE" }); // sin perfil elegido el panel exige elegir (needsChoice)
  });

  it("jamás se muestra «No validado» para un destino habilitado ni una acción para uno no habilitado, en ninguna combinación", () => {
    const sessions = [null, ...["CREATED", "WAITING_FOR_AGENT", "IN_PROGRESS", "WAITING_FOR_USER", "HUMAN_CONTROL", "SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"].map((status) => ({
      status, destination: "SIRE_RVIE", diagnostic_code: null, is_terminal: ["SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"].includes(status),
    }))];
    for (const enabled of [true, false]) {
      for (const permission of [null, { available: true, proven: true }, { available: false, proven: false }]) {
        for (const session of sessions) {
          const state = destinationUiState({ ...rvie, enabled }, session, permission);
          if (enabled) expect(state.key).not.toBe("NOT_VALIDATED");
          if (!enabled) expect(state).toMatchObject({ key: "NOT_VALIDATED", canOpen: false });
          if (["IN_PROGRESS", "NEEDS_HUMAN", "HANDED_OVER", "NEEDS_PROOF", "NOT_VALIDATED"].includes(state.key)) expect(state.canOpen).toBe(false);
          if (session?.status === "FAILED" && enabled && permission?.available !== false) expect(state.key).toBe("FAILED");
        }
      }
    }
  });

  const row = (id: string, phase: string, created_at: string, destination = "DECLARACIONES_Y_PAGOS", credential_id = "p1") => ({ id, destination, credential_id, phase, created_at });

  it("un éxito posterior reemplaza la alerta del fallo anterior en la vista principal, y el fallo permanece en el historial", () => {
    const rows = [row("a2", "CONFIRMED_PASS", "2026-10-14T09:36:00Z"), row("a1", "CONFIRMED_FAIL", "2026-10-14T03:49:00Z")];
    expect(proofHeadline(rows, "DECLARACIONES_Y_PAGOS", "p1")).toMatchObject({ key: "VALIDATED" });
    expect(currentFailures(rows)).toEqual([]); // sin alerta roja
    expect(rows).toHaveLength(2); // nada se borra: el historial conserva ambos intentos
    expect(acceptanceStatusLabel({ phase: "CONFIRMED_FAIL", result_source: "SYSTEM" })).toContain("falló");
  });

  it("el resultado vigente es el de la ÚLTIMA prueba por destino y perfil, sin mezclar destinos ni credenciales", () => {
    const rows = [
      row("b3", "CONFIRMED_FAIL", "2026-10-15T09:00:00Z", "SIRE_RVIE", "p1"), // un fallo POSTERIOR sí se muestra
      row("b2", "CONFIRMED_PASS", "2026-10-14T09:00:00Z", "SIRE_RVIE", "p1"),
      row("b1", "CONFIRMED_FAIL", "2026-10-14T03:00:00Z", "SIRE_RCE", "p2"), // otro destino y otro perfil
      row("b0", "REVOKED", "2026-10-16T09:00:00Z", "SIRE_RVIE", "p1"), // una autorización revocada no cuenta como resultado
    ];
    expect(proofHeadline(rows, "SIRE_RVIE", "p1")).toMatchObject({ key: "FAILED" });
    expect(currentFailures(rows).map((r) => r.id).sort()).toEqual(["b1", "b3"]);
    expect(proofHeadline(rows, "SIRE_RCE", "p1")).toBeNull();
    expect(proofHeadline([row("c1", "AUTHORIZED", "2026-10-14T09:00:00Z")], "DECLARACIONES_Y_PAGOS", "p1")).toMatchObject({ key: "PENDING" });
    expect(proofHeadline([row("c2", "ARRIVAL_AWAITING_CONFIRMATION", "2026-10-14T09:00:00Z")], "DECLARACIONES_Y_PAGOS", "p1")).toMatchObject({ key: "AWAITING" });
  });

  it("una sesión fallida nunca ofrece confirmar y el rechazo por perfil sin prueba se explica sin culpar a la credencial", () => {
    expect(canConfirmAcceptance({ phase: "SESSION_FAILED" })).toBe(false);
    expect(canConfirmAcceptance({ phase: "CONFIRMED_FAIL" })).toBe(false);
    const message = describeStartError({ code: "SOL_PROFILE_NOT_PROVEN" });
    expect(message).toContain("no se extiende");
    expect(describeStartError({ code: "SOL_DESTINATION_DISABLED" })).toContain("Menú SOL");
  });

  it("el panel de aceptación usa la vista vigente y los estados provienen del servidor, no se deducen del texto", () => {
    const panel = read("src/components/sunat/AcceptancePanel.tsx");
    expect(panel).toContain("currentFailures(rows)");
    expect(panel).not.toContain('r.phase === "SESSION_FAILED" ||'); // la alerta ya no recorre TODOS los fallos historicos
    expect(panel).toContain("canConfirmAcceptance");
    const page = read("src/app/(app)/clients/[id]/sol-access/page.tsx");
    expect(page).toContain("destinations.filter(openable)"); // un perfil sin prueba no recibe boton de apertura
  });
});
