import { hasPermission } from "@/lib/permissions";
import type { User } from "@/lib/types/auth";

export const COCKPIT_HREF = "/cockpit";

// El cockpit es solo lectura y reutiliza el permiso de lectura del tablero (sin permisos nuevos).
export function canViewCockpit(user: User | null | undefined): boolean {
  return hasPermission(user, "dashboard:read");
}
