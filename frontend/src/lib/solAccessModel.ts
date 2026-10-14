// Modelo PURO de vista del acceso SOL supervisado (Fase 5, Bloque 5.2, Domain F). Sin JSX ni red, para probarse con
// vitest (entorno Node). El backend es la UNICA autoridad del estado: aqui solo se traduce el estado ya autoritativo a
// texto y a que controles mostrar. Nunca se infiere exito de un HTTP 200, un temporizador ni del estado del job.
import type {
  SolDestinationPermission, SolDestinationInfo, SolAccessSessionOut, SolAgentsOut, SolDestination, SolSessionStatus } from "@/lib/types/solAccess";
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
  SIRE: "SIRE",
  SIRE_RVIE: "SIRE · Registro de Ventas (RVIE)",
  SIRE_RCE: "SIRE · Registro de Compras (RCE)",
  SIRE_GENERACION_REGISTROS: "SIRE · Generación de Registros Electrónicos",
  DECLARACIONES_Y_PAGOS: "Declaraciones y Pagos",
  DECLARA_FACIL: "Declara Fácil",
  TRAMITES_Y_CONSULTAS: "Trámites y Consultas",
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
  PORTAL_CHANGED: "SUNAT mostró una pantalla distinta de la esperada y Luis IA se detuvo por seguridad (no implica que SUNAT haya cambiado su portal; mira el diagnóstico).",
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
  DESTINATION_NOT_VALIDATED:
    "Este destino de SUNAT todavía no está validado con evidencia real, por eso Luis IA no lo abre automáticamente. Usa «Abrir Menú SOL» y navega tú dentro de tus permisos.",
  DESTINATION_ENTRY_NOT_FOUND:
    "El destino no aparece en el menú de esta cuenta SOL. Puede que la clave no tenga permiso para él o que SUNAT haya cambiado el menú; Luis IA no puede distinguirlo sin evidencia. Usa «Abrir Menú SOL».",
  DESTINATION_MENU_STRUCTURE_MISMATCH:
    "La ruta del menú hacia el destino ya no coincide con la validada (SUNAT pudo cambiar el menú). No se hizo ningún clic fuera de lo autorizado. Usa «Abrir Menú SOL».",
  DESTINATION_IDENTITY_MISMATCH:
    "Luis IA llegó a una pantalla que no se identifica como el destino pedido y se detuvo. Usa «Abrir Menú SOL» y revisa el navegador.",
  DESTINATION_FRAME_NOT_VALIDATED:
    "El contenido del destino no cumplió la identidad estructural esperada y no se entregó como válido. No se ejecutó ninguna acción en SUNAT.",
  DESTINATION_PAGE_NOT_VALIDATED:
    "SUNAT abrió una ventana que no coincide con el destino autorizado (o abrió más de una), por eso Luis IA se detuvo. No se ejecutó ninguna acción.",
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
  SOL_CREDENTIAL_SELECTION_REQUIRED: "Este cliente tiene varios perfiles de credencial: elige con cuál se abrirá el acceso.",
  SOL_PROFILE_NOT_PROVEN:
    "Este perfil de credencial aún no superó la prueba real de este destino (la autorización de una credencial no se extiende a otras). El propietario puede autorizar una prueba desde el Centro SUNAT; Luis IA no probará otra credencial por su cuenta.",
  SOL_DESTINATION_DISABLED: "Este destino no está habilitado: ábrelo manualmente desde el Menú SOL.",
  SOL_CREDENTIAL_NOT_FOUND: "El perfil de credencial elegido ya no está disponible. Elige otro desde la lista.",
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
  "SOL_CREDENTIAL_SELECTION_REQUIRED",
  "SOL_PROFILE_NOT_PROVEN",
  "SOL_DESTINATION_DISABLED",
  "SOL_CREDENTIAL_NOT_FOUND",
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
  /** M3: el cliente tiene VARIOS perfiles y el usuario aun no eligio uno (nunca se elige por el usuario). */
  selectionRequired?: boolean;
}

const USABLE_CREDENTIAL_STATUSES = new Set(["NEEDS_VALIDATION", "ACTIVE"]);

export function evaluatePreflight(input: PreflightInput): { checks: PreflightCheck[]; canStart: boolean } {
  const { canStart, credential, vault, agents, session } = input;
  const selectionRequired = input.selectionRequired === true && credential == null;
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
        selectionRequired
          ? "Elige con qué credencial se abrirá el acceso: este cliente tiene varios perfiles."
          : credential == null
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


// --- M3: estado de cada destino en la UI (catalogo del backend + sesion actual) ---

export type DestinationUiKey =
  | "AVAILABLE"
  | "NOT_VALIDATED"
  | "IN_PROGRESS"
  | "NEEDS_HUMAN"
  | "HANDED_OVER"
  | "FINISHED"
  | "FAILED"
  | "NOT_AUTHORIZED"
  | "NEEDS_PROOF"
  | "CANCELLED";

export interface DestinationUiState {
  key: DestinationUiKey;
  label: string;
  hint: string;
  canOpen: boolean; // solo un destino HABILITADO, sin otra sesion activa, puede ofrecerse como accion
}

const NOT_VALIDATED_HINT =
  "Aún no está validado con evidencia real, por eso Luis IA no lo abre automáticamente. Usa «Abrir Menú SOL» y navega tú dentro de tus permisos.";
const ARRIVAL_ONLY_HINT =
  "Se observó cómo se ve la pantalla de llegada, pero falta confirmar cómo llegar hasta ella desde el menú. Por ahora no se abre automáticamente: usa «Abrir Menú SOL» y navega tú dentro de tus permisos.";
const RECON_COMPLETE_HINT =
  "El reconocimiento real ya identificó la ruta de menú y la pantalla de llegada, pero aún falta aceptar la apertura automática en vivo con una credencial autorizada. Por ahora usa «Abrir Menú SOL» y navega tú dentro de tus permisos.";
const OPEN_ONLY_HINT =
  "Solo se prevé la apertura supervisada de la pantalla inicial; cargar, generar, presentar, enviar, declarar, pagar o rectificar siempre queda en tus manos. Aunque la ruta ya está reconocida, el acceso se mantiene manual hasta aceptar su apertura en vivo.";

function notValidatedHint(info: Pick<SolDestinationInfo, "evidence" | "open_only">): string {
  if (info.open_only) return OPEN_ONLY_HINT;
  if (info.evidence === "RECON_COMPLETE") return RECON_COMPLETE_HINT;
  return info.evidence === "ARRIVAL_ONLY" ? ARRIVAL_ONLY_HINT : NOT_VALIDATED_HINT;
}

// Un destino no habilitado jamas ofrece accion. "No autorizado" solo se muestra cuando el backend lo COMPRUEBA (codigo dedicado); la
// ausencia de la entrada en el menu NO basta para afirmarlo (puede ser un cambio del portal): se informa como fallo con su diagnostico.
export function destinationUiState(
  info: Pick<SolDestinationInfo, "id" | "enabled"> & Partial<Pick<SolDestinationInfo, "evidence" | "open_only" | "requires_profile_proof">>,
  session: Pick<SolAccessSessionOut, "status" | "destination" | "diagnostic_code" | "is_terminal"> | null | undefined,
  permission?: Pick<SolDestinationPermission, "available" | "proven"> | null,
): DestinationUiState {
  if (!info.enabled) return { key: "NOT_VALIDATED", label: "No validado", hint: notValidatedHint(info), canOpen: false };
  const active = session != null && !isTerminalSession(session);
  const activeHere = active && session?.destination === info.id;
  // La estructura del portal esta validada, pero la autorizacion de una credencial NO se extrapola: el perfil elegido necesita su propia prueba real.
  if (info.requires_profile_proof && permission && permission.available === false && !activeHere) {
    return {
      key: "NEEDS_PROOF",
      label: "Requiere prueba con este perfil",
      hint: "La estructura de este destino está validada, pero este perfil aún no superó su propia prueba real. El propietario puede autorizarla desde el Centro SUNAT; Luis IA no prueba otra credencial por su cuenta.",
      canOpen: false,
    };
  }
  if (session && session.destination === info.id) {
    const s = session.status;
    if (s === "WAITING_FOR_USER") return { key: "NEEDS_HUMAN", label: "Requiere tu intervención", hint: "Resuelve el aviso o la verificación en el navegador local.", canOpen: false };
    if (s === "HUMAN_CONTROL") return { key: "HANDED_OVER", label: "Navegador entregado", hint: "La automatización terminó: el control es tuyo.", canOpen: false };
    if (active) return { key: "IN_PROGRESS", label: "Sesión en curso", hint: "Luis IA está autenticando y navegando.", canOpen: false };
    if (s === "SUCCEEDED") return { key: "FINISHED", label: "Finalizado", hint: "El navegador local ya se cerró.", canOpen: true };
    if (s === "CANCELLED") return { key: "CANCELLED", label: "Cancelado", hint: "", canOpen: true };
    if (session.diagnostic_code === "DESTINATION_NOT_AUTHORIZED") {
      return { key: "NOT_AUTHORIZED", label: "No autorizado para esta cuenta", hint: "La cuenta SOL no tiene permiso para este destino.", canOpen: true };
    }
    return { key: "FAILED", label: "Fallido", hint: "Revisa el diagnóstico de la sesión. Puedes usar «Abrir Menú SOL».", canOpen: true };
  }
  return active
    ? { key: "AVAILABLE", label: "Disponible (otra sesión activa)", hint: "Espera a que termine la sesión en curso: no se duplican autenticaciones.", canOpen: false }
    : { key: "AVAILABLE", label: "Disponible y validado", hint: "", canOpen: true };
}


// --- M3: perfiles de credencial y cuatro estados que nunca son equivalentes ---

/** Elegible para iniciar un acceso (estado utilizable). Un perfil no utilizable JAMAS se sustituye por otro. */
export function usableProfiles<T extends { status: string }>(profiles: T[]): T[] {
  return profiles.filter((p) => USABLE_CREDENTIAL_STATUSES.has(p.status));
}

/** Con un solo perfil se usa ese (compatibilidad M2); con varios el usuario DEBE elegir: devuelve el elegido o null. */
export function resolveSelectedProfile<T extends { id: string }>(profiles: T[], chosenId: string | null): T | null {
  if (profiles.length === 1) return profiles[0];
  return profiles.find((p) => p.id === chosenId) ?? null;
}

export function selectionRequiredFor(profiles: { id: string }[], chosenId: string | null): boolean {
  return profiles.length > 1 && !profiles.some((p) => p.id === chosenId);
}

const PERMITTED_LABEL: Record<string, string> = {
  UNKNOWN: "sin observar con este perfil",
  OBSERVED_ALLOWED: "observado: el portal lo permitió",
  OBSERVED_NOT_IN_MENU: "observado: no aparece en el menú de este perfil",
};
const RECOGNIZED_LABEL: Record<string, string> = {
  COMPLETE: "ruta y llegada reconocidas",
  RECON_COMPLETE: "ruta y llegada reconocidas con evidencia real (falta la aceptación en vivo)",
  LIVE_ACCEPTED: "ruta y llegada reconocidas y aceptadas en vivo por el propietario",
  ARRIVAL_ONLY: "solo la llegada reconocida (falta la entrada)",
  ALIAS: "es el mismo destino que otro",
  CONTAINED: "está dentro de otro destino",
  NONE: "sin reconocimiento",
};

/** Texto de los CUATRO estados de un destino para un perfil: reconocido / habilitado / permitido / completado. */
export function describeDestinationStates(p: { recognized: string; enabled: boolean; permitted: string; completed: boolean; proven?: boolean }): string {
  return [
    `Reconocido: ${RECOGNIZED_LABEL[p.recognized] ?? p.recognized}`,
    `Habilitado en Luis IA: ${p.enabled ? "sí" : "no"}`,
    ...(p.proven === undefined ? [] : [`Probado con este perfil: ${p.proven ? "sí" : "no"}`]),
    `Permitido para este perfil: ${PERMITTED_LABEL[p.permitted] ?? p.permitted}`,
    `Acceso completado: ${p.completed ? "sí" : "no"}`,
  ].join(" · ");
}

// --- M3: aceptacion real controlada ---
/** Texto del historial de una prueba segun la FASE que calcula el servidor (autorizacion / consumo / fallo / llegada real / confirmacion humana). */
export function acceptanceStatusLabel(row: { phase: string; result?: "PASS" | "FAIL" | null; result_source?: "OWNER" | "SYSTEM" | null }): string {
  switch (row.phase) {
    case "AUTHORIZED":
      return "autorizada, aún sin usar";
    case "REVOKED":
      return "revocada";
    case "EXPIRED":
      return "vencida";
    case "CONSUMED_RUNNING":
      return "usada · sesión en curso";
    case "SESSION_FAILED":
      return "usada · la sesión falló antes de llegar al destino";
    case "ARRIVAL_AWAITING_CONFIRMATION":
      return "usada · llegada validada por el agente · falta tu confirmación";
    case "CONFIRMED_PASS":
      return "usada · llegada validada · confirmada como correcta por el propietario";
    case "CONFIRMED_FAIL":
      return row.result_source === "SYSTEM" ? "usada · falló antes de llegar al destino (registrado por el sistema)" : "usada · el propietario indicó que no coincide";
    default:
      return row.phase;
  }
}

/** SOLO una llegada realmente validada y entregada al propietario puede recibir una confirmacion (positiva o negativa). */
export function canConfirmAcceptance(row: { phase: string }): boolean {
  return row.phase === "ARRIVAL_AWAITING_CONFIRMATION";
}

/** SOLO una llegada realmente validada y entregada al propietario puede recibir una confirmacion (positiva o negativa). */
export type ProofHeadlineKey = "VALIDATED" | "AWAITING" | "PENDING" | "FAILED";

export interface ProofRowLite {
  id: string;
  destination: string;
  credential_id: string;
  phase: string;
  created_at: string;
  result_source?: "OWNER" | "SYSTEM" | null;
  diagnostic_code?: string | null;
}

const PHASE_TO_HEADLINE: Record<string, ProofHeadlineKey | undefined> = {
  AUTHORIZED: "PENDING",
  CONSUMED_RUNNING: "PENDING",
  ARRIVAL_AWAITING_CONFIRMATION: "AWAITING",
  CONFIRMED_PASS: "VALIDATED",
  SESSION_FAILED: "FAILED",
  CONFIRMED_FAIL: "FAILED",
};

/**
 * Resultado VIGENTE de un destino para un perfil: el de la ultima prueba (la mas reciente que no este revocada ni vencida). Un intento fallido anterior
 * queda en el historial pero JAMAS tapa un exito posterior; un fallo posterior a un exito sigue visible.
 */
export function proofHeadline<T extends ProofRowLite>(rows: T[], destination: string, credentialId: string): { key: ProofHeadlineKey; row: T } | null {
  const relevant = rows
    .filter((r) => r.destination === destination && r.credential_id === credentialId && PHASE_TO_HEADLINE[r.phase] !== undefined)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
  const latest = relevant[0];
  return latest ? { key: PHASE_TO_HEADLINE[latest.phase] as ProofHeadlineKey, row: latest } : null;
}

/** Pares (destino, perfil) cuyo resultado vigente es un fallo: unicos que justifican una alerta roja en la vista principal. */
export function currentFailures<T extends ProofRowLite>(rows: T[]): T[] {
  const keys = new Set(rows.map((r) => `${r.destination}|${r.credential_id}`));
  const out: T[] = [];
  for (const key of keys) {
    const [destination, credentialId] = key.split("|");
    const head = proofHeadline(rows, destination, credentialId);
    if (head && head.key === "FAILED") out.push(head.row);
  }
  return out;
}
