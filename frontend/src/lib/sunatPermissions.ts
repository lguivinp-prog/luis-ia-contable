// Gateo por permiso de las acciones SUNAT del producto M2. Solo UX: el backend es la frontera de seguridad. Composicion de
// permisos EXISTENTES (sin permisos nuevos); el asistente no tiene ninguno de los de credencial/sesion SOL.
import { hasPermission } from "@/lib/permissions";
import type { User } from "@/lib/types/auth";

// Abrir un destino (Menu SOL / Ficha RUC) sin extraer: crear la sesion + poder leer la credencial.
export function canOpenSunatDestination(user: User | null | undefined): boolean {
  return hasPermission(user, "sol_session:create") && hasPermission(user, "sol_credential:read");
}

// Actualizar la Ficha propone cambios al cliente: ademas exige poder editarlo.
export function canRefreshFicha(user: User | null | undefined): boolean {
  return canOpenSunatDestination(user) && hasPermission(user, "client:write");
}

export function canReviewFicha(user: User | null | undefined): boolean {
  return hasPermission(user, "client:write");
}

// Alta asistida: crea el cliente provisional, guarda la credencial en la boveda e inicia la sesion.
export function canOnboardAssisted(user: User | null | undefined): boolean {
  return (
    hasPermission(user, "client:write") &&
    hasPermission(user, "sol_credential:create") &&
    hasPermission(user, "sol_session:create") &&
    hasPermission(user, "sol_credential:read")
  );
}
