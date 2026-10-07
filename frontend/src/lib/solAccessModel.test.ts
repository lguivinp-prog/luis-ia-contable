import { describe, expect, it } from "vitest";

import {
  availableActions,
  classifyStartFailure,
  createStartGuard,
  describeCancelError,
  describeSessionState,
  describeStartError,
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
    expires_at: "", version: 1, is_terminal: terminal, ...extra,
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
