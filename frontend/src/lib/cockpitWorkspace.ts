// Logica PURA del workspace cliente/periodo (M1): pestanas, estado en la URL, payloads y errores de los estados
// manuales. Sin red ni JSX. La derivacion de estados vive en el backend.
import { ApiError } from "@/lib/apiClient";
import {
  ACCOUNTING_STATUS_SEMAPHORE,
  BUZON_STATUS_SEMAPHORE,
  COMMUNICATION_STATUS_SEMAPHORE,
} from "@/lib/cockpitPresentation";
import { hasPermission } from "@/lib/permissions";
import type { User } from "@/lib/types/auth";
import type { OperationsState, OperationsUpdate, WorkspaceDeclaration } from "@/lib/types/cockpit";

export type WorkspaceTabKey =
  | "resumen"
  | "buzon"
  | "documentos"
  | "sire"
  | "declaraciones"
  | "contabilidad"
  | "pagos"
  | "honorarios"
  | "comunicacion"
  | "historial";

export interface WorkspaceTab {
  key: WorkspaceTabKey;
  label: string;
}

export const WORKSPACE_TABS: WorkspaceTab[] = [
  { key: "resumen", label: "Resumen" },
  { key: "buzon", label: "Apertura / Buzón" },
  { key: "documentos", label: "Documentos" },
  { key: "sire", label: "SIRE" },
  { key: "declaraciones", label: "Declaraciones" },
  { key: "contabilidad", label: "Contabilidad" },
  { key: "pagos", label: "Pagos" },
  { key: "honorarios", label: "Honorarios" },
  { key: "comunicacion", label: "Comunicación" },
  { key: "historial", label: "Historial" },
];

export const DEFAULT_WORKSPACE_TAB: WorkspaceTabKey = "resumen";

export function parseWorkspaceTab(value: string | null | undefined): WorkspaceTabKey {
  return WORKSPACE_TABS.find((tab) => tab.key === value)?.key ?? DEFAULT_WORKSPACE_TAB;
}

// Pestanas visibles segun permisos de LECTURA reales (el backend sigue siendo la autoridad).
export function visibleWorkspaceTabs(user: User | null | undefined): WorkspaceTab[] {
  return WORKSPACE_TABS.filter((tab) => tab.key !== "honorarios" || hasPermission(user, "professional_fee:read"));
}

export function workspaceUrl(clientId: string, periodId: string, tab: WorkspaceTabKey): string {
  const params = new URLSearchParams({ period: periodId });
  if (tab !== DEFAULT_WORKSPACE_TAB) params.set("tab", tab);
  return `/cockpit/${clientId}?${params.toString()}`;
}

export function cockpitBackUrl(): string {
  return "/cockpit";
}

export const SIRE_OBLIGATION_CODES = ["SIRE-RVIE", "SIRE-RCE"];

export function isSireDeclaration(declaration: Pick<WorkspaceDeclaration, "obligation_code">): boolean {
  return SIRE_OBLIGATION_CODES.includes(declaration.obligation_code);
}

export function sireDeclarations(declarations: WorkspaceDeclaration[]): WorkspaceDeclaration[] {
  return declarations.filter(isSireDeclaration);
}

export type ManualField = "buzon_status" | "accounting_status" | "client_communication_status";

// Un solo campo por solicitud, siempre con la version leida (concurrencia optimista).
export function buildStatusUpdate(current: OperationsState, field: ManualField, value: string): OperationsUpdate {
  return { expected_row_version: current.row_version, [field]: value } as OperationsUpdate;
}

export function buildNotesUpdate(current: OperationsState, notes: string): OperationsUpdate {
  const trimmed = notes.trim();
  return { expected_row_version: current.row_version, notes: trimmed === "" ? null : trimmed };
}

export const CONFLICT_CODE = "CLIENT_PERIOD_OPERATIONS_VERSION_CONFLICT";

export function isVersionConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 409) return false;
  const detail = error.detail as { code?: string } | null;
  return detail?.code === CONFLICT_CODE;
}

export interface OperationsErrorInfo {
  message: string;
  reload: boolean; // true => hay que recargar el workspace (otro usuario cambio los datos)
}

export function describeOperationsError(error: unknown): OperationsErrorInfo {
  if (isVersionConflict(error)) {
    return {
      message: "Otra persona modificó este periodo mientras trabajabas. Se recargaron los datos; revisa y repite el cambio.",
      reload: true,
    };
  }
  if (error instanceof ApiError && error.status === 403) {
    return { message: "No tienes permiso para cambiar estos estados.", reload: false };
  }
  if (error instanceof ApiError && error.status === 404) {
    return { message: "El cliente o el periodo ya no existe.", reload: true };
  }
  return { message: "No se pudo guardar el cambio. Inténtalo de nuevo.", reload: false };
}

export function lastUpdateLabel(operations: OperationsState): string | null {
  if (operations.row_version === 0 || !operations.updated_at) return null;
  const date = new Date(operations.updated_at).toLocaleString("es-PE");
  return operations.updated_by_name ? `Actualizado por ${operations.updated_by_name} · ${date}` : `Actualizado ${date}`;
}

export const HISTORY_FIELD_LABEL: Record<string, string> = {
  buzon_status: "Buzón",
  accounting_status: "Contabilidad",
  client_communication_status: "Comunicación",
  notes: "Notas",
};

export const MANUAL_STATE_NOTICE =
  "Estos estados son registrados manualmente por el equipo. No se leen de SUNAT, CONTASIS ni DARWIN.";
export const ACCOUNTING_DETAIL_NOTICE = "El detalle contable permanece en CONTASIS/DARWIN.";
export const BUZON_SYNC_NOTICE =
  "La sincronización automática del Buzón SUNAT no está activa. Registra aquí, manualmente, el resultado de tu revisión.";
export const COMMUNICATION_NOTICE =
  "Estado manual. Marcar como enviado un borrador de WhatsApp no demuestra por sí solo la comunicación mensual al cliente.";

// Valores del historial con la misma etiqueta que ve el usuario en los controles (no codigos internos).
export function historyValueLabel(field: string, value: string | null): string {
  if (value === null) return "—";
  const map =
    field === "buzon_status"
      ? BUZON_STATUS_SEMAPHORE
      : field === "accounting_status"
        ? ACCOUNTING_STATUS_SEMAPHORE
        : field === "client_communication_status"
          ? COMMUNICATION_STATUS_SEMAPHORE
          : null;
  return map?.[value]?.label ?? value;
}
