import { describe, expect, it } from "vitest";

import {
  availableActions,
  classifyStartFailure,
  createStartGuard,
  describeCancelError,
  describeSession,
  describeSessionState,
  DESTINATION_LABEL,
  visibleDestinations,
  describeStartError,
  describeDiagnostic,
  describeTerminalReason,
  evaluatePreflight,
  FORBIDDEN_POST_LANDING_ACTIONS,
  isTerminalSession,
  nextPollDelayMs,
  shouldPoll,
  SESSION_STATE_COPY,
  WAITING_FOR_USER_INSTRUCTIONS,
} from "@/lib/solAccessModel";
import { canCancelSolSession, canStartSolSession, canViewSupervisedSolAccess } from "@/lib/solAccessPermissions";
import type { SolAccessSessionOut, SolAgentsOut } from "@/lib/types/solAccess";
import type { SolCredentialStatusOut, SolVaultStatusOut } from "@/lib/types/solVault";
import type { User } from "@/lib/types/auth";

function session(status: string, extra: Partial<SolAccessSessionOut> = {}): SolAccessSessionOut {
  const terminal = ["SUCCEEDED", "PARTIAL_FAILURE", "FAILED", "CANCELLED", "EXPIRED"].includes(status);
  return {
    id: "s1", client_id: "c1", scope: "AUTHENTICATE_TO_LANDING_ONLY", status, terminal_reason_code: null,
    operation_result: null, created_at: "", started_at: null, waiting_for_user_at: null, completed_at: null,
    expires_at: "", version: 1, is_terminal: terminal, destination: null, extract_ficha: false,
    human_control_at: null, ...extra,
  };
}
const credential = { status: "NEEDS_VALIDATION" } as SolCredentialStatusOut;
const vaultOk = { initialized: true, status: "ACTIVE", unlocked: true } as SolVaultStatusOut;
const agentsOk: SolAgentsOut = {
  broker_enabled: true,
  agents: [{ id: "a", display_name: "A", status: "ACTIVE", paired_at: "", last_seen_at: null, available: true }],
};

describe("estados de sesion", () => {
  it("tiene copy para cada estado y los terminales no se sondean", () => {
    for (const status of Object.keys(SESSION_STATE_COPY)) {
      expect(describeSessionState(status).label.length).toBeGreaterThan(0);
    }
    expect(describeSessionState("SUCCEEDED").description).toBe("Autenticación completada hasta el menú SOL.");
    expect(describeSessionState("WAITING_FOR_USER").description).toBe("Se requiere intervención en la ventana del navegador.");
    for (const s of ["SUCCEEDED", "PARTIAL_FAILURE", "FAILED", "CANCELLED", "EXPIRED"]) {
      expect(isTerminalSession(session(s))).toBe(true);
      expect(shouldPoll(session(s))).toBe(false);
    }
    for (const s of ["CREATED", "WAITING_FOR_AGENT", "IN_PROGRESS", "WAITING_FOR_USER"]) {
      expect(shouldPoll(session(s))).toBe(true);
    }
    expect(shouldPoll(null)).toBe(false);
  });

  it("un estado desconocido usa un mensaje seguro", () => {
    expect(describeSessionState("ALGO_NUEVO").label).toBe("Estado desconocido");
  });

  it("el sondeo tiene backoff acotado", () => {
    expect(nextPollDelayMs(0)).toBe(3000);
    expect(nextPollDelayMs(2)).toBe(12000);
    expect(nextPollDelayMs(50)).toBe(30000);
  });
});

describe("acciones", () => {
  it("cancelar solo en estados activos y con permiso; terminal solo ofrece iniciar otra", () => {
    expect(availableActions(session("IN_PROGRESS"), true)).toEqual(["cancel"]);
    expect(availableActions(session("IN_PROGRESS"), false)).toEqual([]);
    expect(availableActions(session("SUCCEEDED"), true)).toEqual(["start_new"]);
    expect(availableActions(null, true)).toEqual([]);
  });

  it("nunca ofrece acciones posteriores al menu SOL", () => {
    for (const s of ["CREATED", "WAITING_FOR_AGENT", "IN_PROGRESS", "WAITING_FOR_USER", "SUCCEEDED", "FAILED"]) {
      const actions = availableActions(session(s), true) as string[];
      for (const forbidden of FORBIDDEN_POST_LANDING_ACTIONS) expect(actions).not.toContain(forbidden);
    }
  });

  it("la instruccion de WAITING_FOR_USER explica los avisos de SUNAT sin que Luis IA toque los datos de contacto", () => {
    expect(WAITING_FOR_USER_INSTRUCTIONS.join(" ")).toContain("Luis IA nunca confirma ni modifica tus datos de contacto.");
  });

  it("una sesion estancada que se cerro se explica como reintentable y no como 'en curso'", () => {
    expect(describeSessionState("FAILED").terminal).toBe(true);
    expect(describeTerminalReason("JOB_EXPIRED")?.message).toMatch(/Puedes reintentar/);
  });

  it("la instruccion de WAITING_FOR_USER no pide ingresar nada en la web", () => {
    expect(WAITING_FOR_USER_INSTRUCTIONS.join(" ")).toContain("No ingreses credenciales ni códigos en esta página.");
  });
});

describe("motivos terminales", () => {
  it("mapea codigos conocidos y usa generico para los desconocidos", () => {
    expect(describeTerminalReason("AUTH_FAILED")?.message).toContain("SUNAT no aceptó");
    expect(describeTerminalReason(null)).toBeNull();
    const unknown = describeTerminalReason("MOTIVO_NUEVO");
    expect(unknown?.message).toBe("El acceso terminó por un motivo no reconocido.");
    expect(unknown?.supportCode).toBe("MOTIVO_NUEVO");
  });

  it("no expone como codigo cadenas con forma de secreto", () => {
    const odd = describeTerminalReason("clave: abc 123 /x");
    expect(odd?.supportCode).toBeNull();
    expect(odd?.message).not.toContain("abc");
  });
});

describe("errores de acciones", () => {
  it("mapea codigos permitidos y no filtra el cuerpo crudo", () => {
    expect(describeStartError({ code: "SOL_VAULT_LOCKED" })).toContain("bóveda está bloqueada");
    expect(describeStartError({ code: "SOL_AGENT_UNAVAILABLE" })).toContain("agente local");
    expect(describeStartError({ code: "X", secret: "p4ss" })).not.toContain("p4ss");
    expect(describeCancelError({ code: "SOL_SESSION_TERMINAL" })).toBe("La sesión ya había terminado.");
    expect(describeCancelError(undefined)).toContain("No se pudo");
  });
});

describe("idempotencia / doble clic", () => {
  it("un doble clic produce un solo inicio en vuelo con la misma clave", () => {
    let n = 0;
    const guard = createStartGuard(() => `k${++n}`);
    expect(guard.begin()).toBe("k1");
    expect(guard.begin()).toBeNull();
    guard.finish("unknown");
    expect(guard.begin()).toBe("k1"); // red incierta: se reutiliza la clave
    guard.finish("rejected");
    expect(guard.begin()).toBe("k2"); // rechazo definitivo: clave nueva
  });

  it("clasifica rechazos definitivos vs inciertos", () => {
    expect(classifyStartFailure({ code: "SOL_VAULT_LOCKED" })).toBe("rejected");
    expect(classifyStartFailure(undefined)).toBe("unknown");
  });
});

describe("preflight", () => {
  const base = { canStart: true, credential, vault: vaultOk, agents: agentsOk, session: null };
  it("habilita con todo listo", () => {
    expect(evaluatePreflight(base).canStart).toBe(true);
  });
  it("bloquea por cada requisito faltante", () => {
    expect(evaluatePreflight({ ...base, canStart: false }).canStart).toBe(false);
    expect(evaluatePreflight({ ...base, credential: null }).canStart).toBe(false);
    expect(evaluatePreflight({ ...base, credential: { status: "INVALID" } as SolCredentialStatusOut }).canStart).toBe(false);
    expect(evaluatePreflight({ ...base, vault: { ...vaultOk, unlocked: false } }).canStart).toBe(false);
    expect(evaluatePreflight({ ...base, agents: { broker_enabled: true, agents: [] } }).canStart).toBe(false);
    expect(evaluatePreflight({ ...base, agents: { broker_enabled: false, agents: [] } }).canStart).toBe(false);
    expect(evaluatePreflight({ ...base, session: session("IN_PROGRESS") }).canStart).toBe(false);
    expect(evaluatePreflight({ ...base, session: session("SUCCEEDED") }).canStart).toBe(true);
  });
});

describe("permisos", () => {
  const mk = (permissions: string[]) => ({ permissions }) as unknown as User;
  it("asistente sin permisos no ve ni inicia", () => {
    expect(canViewSupervisedSolAccess(mk([]))).toBe(false);
    expect(canStartSolSession(mk(["sol_session:create"]))).toBe(false);
    expect(canStartSolSession(mk(["sol_session:create", "sol_credential:read"]))).toBe(true);
    expect(canCancelSolSession(mk(["sol_session:cancel"]))).toBe(true);
  });
});

describe("M2: control humano y destinos", () => {
  it("HUMAN_CONTROL no es terminal, se sondea y se puede cancelar", () => {
    const hc = session("HUMAN_CONTROL", { destination: "FICHA_RUC" });
    expect(isTerminalSession(hc)).toBe(false);
    expect(shouldPoll(hc)).toBe(true);
    expect(availableActions(hc, true)).toEqual(["cancel"]);
    expect(describeSessionState("HUMAN_CONTROL").label).toBe("El navegador es tuyo");
    expect(describeSession(hc).description).toContain("Ficha RUC");
    expect(describeSession(hc).description).toContain("el control es tuyo");
  });

  it("SUCCEEDED con destino significa que el humano cerro el navegador (no 'autenticacion hasta el menu')", () => {
    const done = session("SUCCEEDED", { destination: "MENU_SOL" });
    expect(describeSession(done).description).toBe("Se abrió Menú SOL y el navegador local ya se cerró.");
    expect(describeSession(session("SUCCEEDED")).description).toBe("Autenticación completada hasta el menú SOL.");
  });

  it("solo se muestran destinos que el servidor habilita Y que la UI conoce (nunca SIRE ni afines)", () => {
    expect(visibleDestinations(["MENU_SOL", "FICHA_RUC"])).toEqual(["MENU_SOL", "FICHA_RUC"]);
    expect(visibleDestinations(["SIRE", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS", "FICHA_RUC"])).toEqual(["FICHA_RUC"]);
    expect(visibleDestinations(null)).toEqual([]);
    expect(Object.keys(DESTINATION_LABEL)).toEqual(["MENU_SOL", "FICHA_RUC"]);
  });
});


describe("diagnostico estructural del fallo", () => {
  it("traduce los codigos de la lista cerrada y distingue identidad, bloqueo y tiempo", () => {
    expect(describeDiagnostic("LOGIN_CONTROL_MISMATCH")?.message).toContain("aún no se pidió ninguna clave");
    expect(describeDiagnostic("BLOCKED_BY_INTERMEDIATE_SCREEN")?.message).toContain("no pulsó nada");
    expect(describeDiagnostic("NAVIGATION_TIMEOUT")?.message).toContain("tardó demasiado");
    expect(describeDiagnostic("SHELL_MARKER_MISSING_OR_AMBIGUOUS")?.message).toContain("Iniciaste sesión");
    expect(describeDiagnostic("FICHA_FRAME_NOT_VALIDATED")?.code).toBe("FICHA_FRAME_NOT_VALIDATED");
    expect(describeDiagnostic("LANDING_TRANSIT_UNRESOLVED")?.message).toContain("aún no se pidió ninguna clave");
    expect(describeDiagnostic("LANDING_BEFORE_AUTHENTICATION")?.message).toContain("ya autenticado");
  });

  it("un codigo desconocido con formato valido no inventa texto y uno con formato invalido se descarta", () => {
    expect(describeDiagnostic("CODIGO_NUEVO_DEL_AGENTE")?.message).toBe("Fallo técnico no clasificado.");
    for (const bad of [null, undefined, "", "minusculas", "<script>", "A".repeat(80), "con espacios"]) {
      expect(describeDiagnostic(bad as string | null | undefined), String(bad)).toBeNull();
    }
  });
});


describe("motivos de la espera humana (aviso de validacion de contacto)", () => {
  it("explica al contador que debe resolverlo el y que Luis IA no toca ni confirma nada", () => {
    const contact = describeDiagnostic("CONTACT_VALIDATION_INTERSTITIAL")?.message ?? "";
    expect(contact).toContain("Resuélvela tú");
    expect(contact).toContain("no pulsa «Finalizar»");
    expect(contact).toMatch(/no envía códigos/);
    expect(describeDiagnostic("INFORMATIONAL_MODAL")?.message).toContain("Ciérrala tú");
    expect(describeDiagnostic("HUMAN_ACTION_REQUIRED")?.message).toContain("bloquea el avance");
    expect(describeDiagnostic("FICHA_CONTENT_NOT_READY")?.message).toContain("no se extrajo nada");
  });
});

describe("navegación de autenticación detenida (sin acción humana)", () => {
  it("no pide completar una verificación que no existe", () => {
    const stalled = describeDiagnostic("AUTH_NAVIGATION_STALLED")?.message ?? "";
    expect(stalled).toContain("no mostraba ninguna verificación");
    expect(stalled).toContain("nueva sesión supervisada");
    expect(stalled).not.toContain("Completa la verificación");
    expect(describeDiagnostic("HUMAN_ACTION_REQUIRED")?.message).toContain("Resuélvela en el navegador");
  });
});
