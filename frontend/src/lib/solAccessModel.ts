// Modelo PURO de vista del acceso SOL supervisado (Fase 5, Bloque 5.2, Domain F). Sin JSX ni red, para probarse con
// vitest (entorno Node). El backend es la UNICA autoridad del estado: aqui solo se traduce el estado ya autoritativo a
// texto y a que controles mostrar. Nunca se infiere exito de un HTTP 200, un temporizador ni del estado del job.
import type { SolAccessSessionOut, SolAgentsOut, SolDestination, SolSessionStatus } from "@/lib/types/solAccess";
import type { SolCredentialStatusOut, SolVaultStatusOut } from "@/lib/types/solVault";

export type StateTone = "neutral" | "progress" | "attention" | "success" | "danger";

export interface StateCopy {
  label: string;
  description: string;
  tone: StateTone;
  terminal: boolean;
}

export const SESSION_STATE_COPY: Record<SolSessionStatus, StateCopy> = {
  CREATED: {
    label: "Creada",
    description: "Sesión creada; preparando su envío al agente local.",
    tone: "neutral",
    terminal: false,
  },
  WAITING_FOR_AGENT: {
    label: "Esperando al agente",
    description: "Esperando que el agente local tome la sesión.",
    tone: "neutral",
    terminal: false,
  },
  IN_PROGRESS: {
    label: "En curso",
    description: "Autenticación SOL supervisada en curso.",
    tone: "progress",
    terminal: false,
  },
  HUMAN_CONTROL: {
    label: "El navegador es tuyo",
    description:
      "El destino ya se abrió y la automatización terminó. El navegador local queda bajo tu control: úsalo y ciérralo cuando termines.",
    tone: "attention",
    terminal: false,
  },
  WAITING_FOR_USER: {
    label: "Requiere tu intervención",
    description: "Se requiere intervención en la ventana del navegador.",
    tone: "attention",
    terminal: false,
  },
  SUCCEEDED: {
    label: "Completada",
    description: "Autenticación completada hasta el menú SOL.",
    tone: "success",
    terminal: true,
  },
  PARTIAL_FAILURE: {
    label: "Completada con incidencias",
    description: "La autenticación llegó al menú SOL, pero un paso posterior falló.",
    tone: "attention",
    terminal: true,
  },
  FAILED: {
    label: "Fallida",
    description: "El acceso no pudo completarse.",
    tone: "danger",
    terminal: true,
  },
  CANCELLED: {
    label: "Cancelada",
    description: "Sesión cancelada.",
    tone: "neutral",
    terminal: true,
  },
  EXPIRED: {
    label: "Vencida",
    description: "Sesión vencida.",
    tone: "neutral",
    terminal: true,
  },
};

const UNKNOWN_STATE_COPY: StateCopy = {
  label: "Estado desconocido",
  description: "No se reconoce el estado de la sesión. Recarga la página.",
  tone: "neutral",
  terminal: false,
};

export const TERMINAL_STATUSES: readonly SolSessionStatus[] = ["SUCCEEDED", "PARTIAL_FAILURE", "FAILED", "CANCELLED", "EXPIRED"];
export const CANCELLABLE_STATUSES: readonly SolSessionStatus[] = [
  "CREATED",
  "WAITING_FOR_AGENT",
  "IN_PROGRESS",
  "WAITING_FOR_USER",
  "HUMAN_CONTROL",
];

// --- destinos (M2): etiquetas de los UNICOS destinos validados ---

export const DESTINATION_LABEL: Record<SolDestination, string> = {
  MENU_SOL: "Menú SOL",
  FICHA_RUC: "Ficha RUC",
};

export function isKnownDestination(value: string): value is SolDestination {
  return value in DESTINATION_LABEL;
}

// Solo destinos que el servidor habilita Y que la UI conoce (un destino no validado nunca aparece como accion).
export function visibleDestinations(enabled: readonly string[] | null | undefined): SolDestination[] {
  return (enabled ?? []).filter(isKnownDestination);
}

// Sesion con destino: SUCCEEDED significa que el humano ya cerro el navegador (no "autenticacion hasta el menu").
export function describeSession(session: Pick<SolAccessSessionOut, "status" | "destination">): StateCopy {
  const base = describeSessionState(session.status);
  if (session.destination && session.status === "SUCCEEDED") {
    const where = isKnownDestination(session.destination) ? DESTINATION_LABEL[session.destination] : "el destino";
    return { ...base, label: "Finalizada", description: `Se abrió ${where} y el navegador local ya se cerró.` };
  }
  if (session.destination && isKnownDestination(session.destination) && session.status === "HUMAN_CONTROL") {
    return {
      ...base,
      description: `${DESTINATION_LABEL[session.destination]} está abierto en el navegador local. La automatización terminó: el control es tuyo.`,
    };
  }
  return base;
}

export function describeSessionState(status: string): StateCopy {
  return (SESSION_STATE_COPY as Record<string, StateCopy>)[status] ?? UNKNOWN_STATE_COPY;
}

export function isTerminalSession(session: Pick<SolAccessSessionOut, "status" | "is_terminal"> | null | undefined): boolean {
  if (!session) return false;
  return session.is_terminal === true || (TERMINAL_STATUSES as readonly string[]).includes(session.status);
}

export function isActiveSession(session: SolAccessSessionOut | null | undefined): boolean {
  return session != null && !isTerminalSession(session);
}

// --- sondeo (solo al backend; nunca a SUNAT ni al agente) ---

export const POLL_BASE_MS = 3000;
export const POLL_MAX_MS = 30000;

export function shouldPoll(session: SolAccessSessionOut | null | undefined): boolean {
  return isActiveSession(session);
}

// Intervalo acotado; crece ante fallos consecutivos (perdida de red / reinicio del backend) y vuelve a la base al
// recuperarse.
export function nextPollDelayMs(consecutiveFailures: number): number {
  const failures = Math.max(0, Math.floor(consecutiveFailures));
  return Math.min(POLL_BASE_MS * 2 ** failures, POLL_MAX_MS);
}

// --- acciones permitidas (la UI nunca ofrece acciones posteriores al menu SOL) ---

export type SessionAction = "cancel" | "start_new";

export const FORBIDDEN_POST_LANDING_ACTIONS = [
  "open_mailbox",
  "read_messages",
  "download",
  "declarations",
  "payments",
  "continue",
] as const;

export function availableActions(session: SolAccessSessionOut | null | undefined, canCancel: boolean): SessionAction[] {
  if (!session) return [];
  if (isTerminalSession(session)) return ["start_new"];
  return canCancel && (CANCELLABLE_STATUSES as readonly string[]).includes(session.status) ? ["cancel"] : [];
}

// --- instruccion humana de WAITING_FOR_USER (sin capturar ni reenviar el desafio) ---

export const WAITING_FOR_USER_INSTRUCTIONS: readonly string[] = [
  "La ventana del navegador local requiere una acción tuya.",
  "Completa la verificación únicamente en esa ventana del navegador local.",
  "Si SUNAT muestra un aviso (por ejemplo confirmar teléfono o correo), ciérralo o resuélvelo tú en esa ventana: Luis IA nunca confirma ni modifica tus datos de contacto.",
  "No ingreses credenciales ni códigos en esta página.",
  "No cierres el navegador, salvo que quieras cancelar la sesión.",
];

export function operationalHint(session: SolAccessSessionOut, agents: SolAgentsOut | null): string | null {
  if (session.status === "WAITING_FOR_AGENT" || session.status === "CREATED") {
    if (agents && (!agents.broker_enabled || !agents.agents.some((a) => a.available || a.status === "ACTIVE"))) {
      return "Verifica que el agente local esté instalado y en ejecución.";
    }
    return "Si tarda demasiado, verifica que el agente local esté en ejecución.";
  }
  return null;
}

// --- motivos terminales (lista permitida -> mensajes saneados) ---

const REASON_MESSAGES: Record<string, string> = {
  AGENT_UNAVAILABLE: "El agente local no estuvo disponible.",
  PAIRING_REQUIRED: "El agente local debe emparejarse nuevamente.",
  AGENT_REVOKED: "El agente local fue revocado.",
  JOB_EXPIRED: "La tarea venció o no avanzó a tiempo y se cerró por seguridad. Puedes reintentar.",
  SESSION_EXPIRED: "La sesión venció.",
  JOB_REPLAY_DETECTED: "Se detectó un intento repetido; inicia una sesión nueva.",
  CREDENTIAL_UNAVAILABLE:
    "La credencial no estuvo disponible en el momento de usarla. Verifica que la bóveda siga desbloqueada e inicia una sesión nueva.",
  VAULT_LOCKED: "La bóveda estaba bloqueada. Desbloquéala e inicia una sesión nueva.",
  VAULT_RECOVERY_REQUIRED: "La bóveda requiere recuperación antes de continuar.",
  AUTH_HOST_CHANGED: "El portal cambió su estructura de acceso; se detuvo por seguridad.",
  WRONG_LOGIN_MODE: "El portal no estaba en el modo de acceso esperado; se detuvo por seguridad.",
  AUTH_FAILED: "SUNAT no aceptó la autenticación. Revisa la credencial antes de intentar otra vez.",
  HUMAN_CHALLENGE_TIMEOUT: "La verificación en el navegador no se completó a tiempo.",
  WRONG_TAXPAYER_CONTEXT: "El contribuyente mostrado no coincide con el esperado.",
  PORTAL_CHANGED: "El portal cambió respecto de lo esperado; se detuvo por seguridad.",
  BROWSER_CLOSED: "El navegador se cerró antes de terminar.",
  CANCELLED_BY_USER: "La sesión fue cancelada.",
  CLEANUP_FAILED: "No se pudo cerrar limpiamente el navegador local.",
  BROKER_DISCONNECTED: "Se perdió la conexión con el agente local.",
  AGENT_CRASHED: "El agente local se detuvo inesperadamente.",
  INTERNAL_ERROR: "Ocurrió un error interno al ejecutar el acceso.",
};

const GENERIC_REASON = "El acceso terminó por un motivo no reconocido.";
const SAFE_CODE = /^[A-Z][A-Z0-9_]{2,40}$/;

export interface ReasonDescription {
  message: string;
  // Codigo de soporte: solo si es una constante simple no sensible.
  supportCode: string | null;
}

// --- diagnostico estructural del fallo (lista cerrada del agente; nunca texto ni datos del portal) ---

const DIAGNOSTIC_MESSAGES: Record<string, string> = {
  // motivos de ESPERA humana (el navegador sigue abierto; Luis IA no toca estas pantallas)
  CONTACT_VALIDATION_INTERSTITIAL:
    "SUNAT muestra una pantalla de validación de datos de contacto. Resuélvela tú en la ventana del navegador: Luis IA no pulsa «Finalizar», no envía códigos ni confirma o actualiza tu correo o teléfono.",
  INFORMATIONAL_MODAL: "SUNAT muestra una ventana informativa. Ciérrala tú en el navegador; Luis IA no la toca.",
  HUMAN_ACTION_REQUIRED: "SUNAT requiere una acción tuya (una verificación o una ventana que bloquea el avance). Resuélvela en el navegador; Luis IA no la toca.",
  FICHA_CONTENT_NOT_READY: "Se llegó a «Ficha RUC», pero su contenido no terminó de cargar (no se extrajo nada).",
  ENTRY_LINK_NOT_FOUND: "No se encontró el acceso a «Mi SOL» en la página pública de SUNAT (aún no se pidió ninguna clave).",
  NAVIGATION_TIMEOUT: "SUNAT tardó demasiado en responder durante la navegación.",
  LOGIN_SURFACE_NOT_REACHED: "No se llegó a la pantalla de ingreso de SUNAT (aún no se pidió ninguna clave).",
  LANDING_BEFORE_AUTHENTICATION: "SUNAT mostró un menú ya autenticado antes de ingresar la clave; se detuvo por seguridad.",
  AUTH_NAVIGATION_STALLED:
    "La ventana de autenticación de SUNAT (por ejemplo «Bienvenidos a SUNAT») no terminó de avanzar y no mostraba ninguna verificación que puedas resolver. Luis IA cerró la sesión; la clave ya se había enviado una vez y no se reutiliza. Inicia una nueva sesión supervisada cuando quieras.",
  LANDING_TRANSIT_UNRESOLVED: "SUNAT pasó por una pantalla intermedia que no llegó al formulario de ingreso (aún no se pidió ninguna clave).",
  UNKNOWN_PAGE_OR_HOST: "Se abrió una página que no pertenece al portal esperado.",
  AMBIGUOUS_PORTAL_SURFACE: "Había más de una pantalla de SUNAT y no se pudo saber cuál era la correcta.",
  AUTH_SURFACE_IN_FRAME: "La pantalla de ingreso apareció dentro de otra página; se detuvo por seguridad.",
  LOGIN_PATH_MISMATCH: "La pantalla de ingreso de SUNAT tiene una dirección distinta de la esperada (aún no se pidió ninguna clave).",
  LOGIN_TITLE_MISMATCH: "La pantalla de ingreso de SUNAT tiene un título distinto del esperado (aún no se pidió ninguna clave).",
  LOGIN_CONTROL_MISMATCH: "El formulario de ingreso de SUNAT difiere del esperado (aún no se pidió ninguna clave).",
  LOGIN_SUBMIT_NAME_MISMATCH: "El botón de ingreso de SUNAT difiere del esperado (aún no se pidió ninguna clave).",
  LOGIN_MODE_CONTROLS_MISSING: "No se encontraron los controles para elegir el modo de ingreso por RUC.",
  LOGIN_RUC_VISIBILITY_MISMATCH: "El modo de ingreso por RUC no mostró los campos esperados.",
  LOGIN_MODE_STATE_AMBIGUOUS: "No se pudo saber qué modo de ingreso estaba activo.",
  LOGIN_RUC_MODE_ACTIVATION_FAILED: "No se pudo activar el modo de ingreso por RUC.",
  LOGIN_CHANGED_BEFORE_SUBMIT: "La pantalla de ingreso cambió justo antes de enviar la clave; se detuvo por seguridad.",
  RETURNED_TO_ENTRY_PAGE: "Tras el ingreso SUNAT volvió a la página pública.",
  LANDING_IDENTITY_MISMATCH: "SUNAT abrió una pantalla del menú con una identidad distinta de la esperada.",
  LANDING_PAGE_NOT_FOUND: "No se encontró la pantalla del menú SOL entre las ventanas abiertas.",
  SHELL_MARKER_MISSING_OR_AMBIGUOUS: "Iniciaste sesión, pero la pantalla no tiene la estructura esperada del menú SOL.",
  SHELL_STRUCTURE_MISMATCH: "Iniciaste sesión, pero el menú SOL no tiene la estructura esperada.",
  SHELL_LOGIN_CONTROLS_PRESENT: "La pantalla seguía mostrando campos de ingreso; no se consideró una sesión iniciada.",
  BLOCKED_BY_INTERMEDIATE_SCREEN: "Un aviso de SUNAT cubría el menú y no se resolvió a tiempo. Luis IA no pulsó nada en él.",
  NAVIGATION_BUDGET_EXHAUSTED: "Se agotaron los pasos de navegación permitidos.",
  FICHA_MENU_ENTRY_MISSING_OR_AMBIGUOUS: "Iniciaste sesión, pero no se encontró una única opción «Ficha RUC» en el menú.",
  FICHA_MENU_STRUCTURE_MISMATCH: "El menú hacia «Ficha RUC» tiene una estructura distinta de la esperada.",
  FICHA_FRAME_NOT_VALIDATED: "Se llegó a «Ficha RUC», pero su contenido no coincide con la estructura esperada.",
  FICHA_DESTINATION_MISMATCH: "La pantalla alcanzada no coincide con «Ficha RUC».",
  FICHA_TAXPAYER_NOT_IDENTIFIED: "Se llegó a «Ficha RUC», pero no se pudo identificar el contribuyente.",
  FICHA_REPORT_REJECTED: "El sistema no aceptó los datos extraídos de la Ficha.",
  HUMAN_CONTROL_REPORT_REJECTED: "El sistema no aceptó el traspaso del navegador.",
  DESTINATION_NOT_ALLOWED: "El destino pedido no está autorizado.",
  UNEXPECTED_EXECUTOR_ERROR: "El navegador local tuvo un error inesperado.",
  UNEXPECTED_ERROR: "El agente local tuvo un error inesperado.",
  BROWSER_CLOSED_OR_FAILED: "El navegador local se cerró antes de terminar.",
  CREDENTIALS_REJECTED_BY_PORTAL: "SUNAT rechazó las credenciales.",
  HUMAN_CHALLENGE_TIMEOUT: "Venció el tiempo para completar la verificación en SUNAT.",
  UNCLASSIFIED_PORTAL_CHANGE: "SUNAT mostró algo que no se pudo clasificar; se detuvo por seguridad.",
  CLEANUP_FAILED: "No se pudo cerrar limpiamente el navegador local.",
  CREDENTIAL_UNAVAILABLE: "La credencial no estuvo disponible para el agente.",
  SECOND_SUBMISSION_REFUSED: "Se rechazó un segundo envío de la clave.",
};

export interface DiagnosticDescription {
  message: string;
  code: string;
}

/** Diagnostico saneado del fallo: texto fijo + codigo de soporte (solo si cumple el formato de la lista cerrada). */
export function describeDiagnostic(code: string | null | undefined): DiagnosticDescription | null {
  if (!code || !/^[A-Z0-9_]{3,48}$/.test(code)) return null;
  return { message: DIAGNOSTIC_MESSAGES[code] ?? "Fallo técnico no clasificado.", code };
}

export function describeTerminalReason(code: string | null | undefined): ReasonDescription | null {
  if (!code) return null;
  const known = REASON_MESSAGES[code];
  if (known) return { message: known, supportCode: code };
  return { message: GENERIC_REASON, supportCode: SAFE_CODE.test(code) ? code : null };
}

// --- errores de las acciones (codigos normalizados del backend; nunca el cuerpo crudo) ---

const START_ERROR_MESSAGES: Record<string, string> = {
  SOL_AGENT_UNAVAILABLE: "No hay un agente local disponible. Inicia el agente e inténtalo otra vez.",
  SOL_VAULT_LOCKED: "La bóveda está bloqueada. Desbloquéala primero.",
  SOL_CREDENTIAL_NOT_CONFIGURED: "Este cliente no tiene una credencial SOL utilizable.",
  SOL_SESSION_ALREADY_ACTIVE: "Ya hay una sesión de acceso en curso para este cliente.",
  SOL_SESSION_IDEMPOTENCY_CONFLICT: "No se pudo iniciar: la solicitud entró en conflicto. Recarga la página.",
  SOL_SESSION_NOT_STARTABLE: "No se pudo iniciar la sesión en este momento. Inténtalo otra vez.",
  SOL_BROKER_UNAVAILABLE: "El servicio de acceso SOL supervisado no está habilitado en este entorno.",
  PERMISSION_DENIED: "No tienes permiso para iniciar un acceso SOL.",
  CLIENT_NOT_FOUND: "El cliente no existe.",
};
const CANCEL_ERROR_MESSAGES: Record<string, string> = {
  SOL_SESSION_TERMINAL: "La sesión ya había terminado.",
  SOL_SESSION_NOT_FOUND: "La sesión ya no existe.",
  PERMISSION_DENIED: "No tienes permiso para cancelar la sesión.",
};
const GENERIC_ACTION_ERROR = "No se pudo completar la operación. Inténtalo otra vez.";

function errorCode(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "code" in detail) {
    const code = (detail as { code: unknown }).code;
    return typeof code === "string" ? code : null;
  }
  return null;
}

export function describeStartError(detail: unknown): string {
  const code = errorCode(detail);
  return (code && START_ERROR_MESSAGES[code]) || GENERIC_ACTION_ERROR;
}

export function describeCancelError(detail: unknown): string {
  const code = errorCode(detail);
  return (code && CANCEL_ERROR_MESSAGES[code]) || GENERIC_ACTION_ERROR;
}

// Rechazos DEFINITIVOS: la solicitud no creo ninguna sesion; la siguiente intencion usa una clave nueva.
const DEFINITIVE_START_REJECTIONS = new Set([
  "SOL_AGENT_UNAVAILABLE",
  "SOL_VAULT_LOCKED",
  "SOL_CREDENTIAL_NOT_CONFIGURED",
  "SOL_SESSION_ALREADY_ACTIVE",
  "SOL_BROKER_UNAVAILABLE",
  "PERMISSION_DENIED",
  "CLIENT_NOT_FOUND",
  "SOL_SESSION_NOT_STARTABLE",
]);

// --- idempotencia / doble clic ---

export interface StartGuard {
  // Devuelve la clave a usar, o null si ya hay un inicio en vuelo (doble clic / re-render).
  begin(): string | null;
  // `result`: "created" conserva la clave (un reintento devolveria la misma sesion);
  // "unknown" (red caida / error inesperado) la conserva, porque el backend pudo haber creado la sesion;
  // "rejected" (rechazo definitivo) la descarta.
  finish(result: "created" | "unknown" | "rejected"): void;
  reset(): void;
  readonly inFlight: boolean;
}

export function classifyStartFailure(detail: unknown): "rejected" | "unknown" {
  const code = errorCode(detail);
  return code && DEFINITIVE_START_REJECTIONS.has(code) ? "rejected" : "unknown";
}

export function createStartGuard(newKey: () => string): StartGuard {
  let key: string | null = null;
  let inFlight = false;
  return {
    begin() {
      if (inFlight) return null;
      inFlight = true;
      if (key === null) key = newKey();
      return key;
    },
    finish(result) {
      inFlight = false;
      if (result === "rejected") key = null;
    },
    reset() {
      key = null;
      inFlight = false;
    },
    get inFlight() {
      return inFlight;
    },
  };
}

// --- preflight (UX; el backend vuelve a validar todo) ---

export interface PreflightCheck {
  id: "permission" | "credential" | "vault" | "agent" | "no_active_session";
  label: string;
  ok: boolean;
  detail: string;
}

export interface PreflightInput {
  canStart: boolean;
  credential: SolCredentialStatusOut | null;
  vault: SolVaultStatusOut | null;
  agents: SolAgentsOut | null;
  session: SolAccessSessionOut | null;
}

const USABLE_CREDENTIAL_STATUSES = new Set(["NEEDS_VALIDATION", "ACTIVE"]);

export function evaluatePreflight(input: PreflightInput): { checks: PreflightCheck[]; canStart: boolean } {
  const { canStart, credential, vault, agents, session } = input;
  const credentialOk = credential != null && USABLE_CREDENTIAL_STATUSES.has(credential.status);
  const vaultOk = vault != null && vault.initialized && vault.status === "ACTIVE" && vault.unlocked;
  const agentOk = agents != null && agents.broker_enabled && agents.agents.some((a) => a.available);
  const noActive = !isActiveSession(session);

  const checks: PreflightCheck[] = [
    {
      id: "permission",
      label: "Permiso para iniciar accesos SOL",
      ok: canStart,
      detail: canStart ? "Autorizado." : "Tu rol no puede iniciar accesos SOL.",
    },
    {
      id: "credential",
      label: "Credencial SOL configurada",
      ok: credentialOk,
      detail:
        credential == null
          ? "Este cliente no tiene credencial SOL."
          : credentialOk
            ? "Configurada."
            : "La credencial no está en un estado utilizable.",
    },
    {
      id: "vault",
      label: "Bóveda desbloqueada",
      ok: vaultOk,
      detail:
        vault == null
          ? "No se pudo consultar la bóveda."
          : !vault.initialized
            ? "La bóveda no está inicializada."
            : vault.status !== "ACTIVE"
              ? "La bóveda no está activa."
              : vault.unlocked
                ? "Desbloqueada."
                : "Bloqueada: desbloquéala para continuar.",
    },
    {
      id: "agent",
      label: "Agente local disponible",
      ok: agentOk,
      detail:
        agents == null
          ? "No se pudo consultar el estado del agente."
          : !agents.broker_enabled
            ? "El servicio de acceso supervisado no está habilitado en este entorno."
            : agentOk
              ? "Disponible."
              : "Ningún agente local está conectado y libre.",
    },
    {
      id: "no_active_session",
      label: "Sin otra sesión en curso",
      ok: noActive,
      detail: noActive ? "Libre." : "Ya hay una sesión en curso para este cliente.",
    },
  ];
  return { checks, canStart: checks.every((c) => c.ok) };
}

// --- sondeo: decision de reintento ante fallos (reload autoritativo) ---

export function describeConnectionTrouble(consecutiveFailures: number): string | null {
  return consecutiveFailures >= 1 ? "Reintentando conectar con el servidor para actualizar el estado…" : null;
}
