export type SemaphoreColor = "verde" | "amarillo" | "naranja" | "rojo" | "gris";

export interface SemaphoreEntry {
  label: string;
  color: SemaphoreColor;
}

export const SERVICE_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  prospecto: { label: "Prospecto", color: "gris" },
  activo: { label: "Activo", color: "verde" },
  suspendido: { label: "Suspendido", color: "naranja" },
  pendiente_cierre: { label: "Pendiente de cierre", color: "amarillo" },
  inactivo: { label: "Inactivo", color: "rojo" },
};

export const RUC_STATUS_SEMAPHORE: Record<string, SemaphoreEntry> = {
  ACTIVO: { label: "Activo", color: "verde" },
  BAJA_PROVISIONAL: { label: "Baja provisional", color: "amarillo" },
  BAJA_DEFINITIVA: { label: "Baja definitiva", color: "rojo" },
  BAJA_DE_OFICIO: { label: "Baja de oficio", color: "rojo" },
  SUSPENSION_TEMPORAL: { label: "Suspensión temporal", color: "naranja" },
};

interface StatusSemaphoreProps {
  status: string;
  map: Record<string, SemaphoreEntry>;
}

export function StatusSemaphore({ status, map }: StatusSemaphoreProps) {
  const entry = map[status] ?? { label: status, color: "gris" as const };
  return <span className={`status-pill status-${entry.color}`}>{entry.label}</span>;
}
