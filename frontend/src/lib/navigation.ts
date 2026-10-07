// Modelo PURO de la navegacion principal (M1): grupos operativos, permisos y item activo. Las URLs existentes se
// conservan; solo se reagrupan. No hay entradas para funciones inexistentes (p. ej. Alertas no tiene pagina).
import { COCKPIT_HREF, canViewCockpit } from "@/lib/cockpitNav";
import { hasPermission } from "@/lib/permissions";
import { canViewSolVaultNav } from "@/lib/solVaultPermissions";
import type { User } from "@/lib/types/auth";

export interface NavItem {
  href: string;
  label: string;
}

export interface NavGroup {
  id: "operacion" | "clientes" | "comunicaciones" | "reportes" | "configuracion";
  label: string;
  items: NavItem[];
}

export function buildNavGroups(user: User | null | undefined): NavGroup[] {
  const operacion: NavItem[] = [];
  if (canViewCockpit(user)) operacion.push({ href: COCKPIT_HREF, label: "Cockpit mensual" });
  operacion.push({ href: "/dashboard", label: "Tablero mensual" });
  if (hasPermission(user, "task:read")) {
    operacion.push({ href: "/agenda", label: "Agenda" }, { href: "/tasks", label: "Tareas" });
  }

  const comunicaciones: NavItem[] = hasPermission(user, "communication:draft")
    ? [{ href: "/communications", label: "Comunicaciones" }]
    : [];

  const configuracion: NavItem[] = [
    { href: "/tax-categories", label: "Categorías tributarias" },
    { href: "/tax-schedules", label: "Cronogramas tributarios" },
    { href: "/tax-periods", label: "Periodos tributarios" },
    { href: "/declarations/generate", label: "Generación de declaraciones" },
  ];
  if (hasPermission(user, "user:read")) configuracion.push({ href: "/users", label: "Usuarios" });
  if (hasPermission(user, "integration:connect")) {
    configuracion.push({ href: "/settings/integrations", label: "Integraciones" });
  }
  if (canViewSolVaultNav(user)) configuracion.push({ href: "/settings/sol-vault", label: "Bóveda SOL" });
  configuracion.push({ href: "/security", label: "Seguridad" });

  const groups: NavGroup[] = [
    { id: "operacion", label: "Operación", items: operacion },
    { id: "clientes", label: "Clientes", items: [{ href: "/clients", label: "Contribuyentes" }] },
    { id: "comunicaciones", label: "Comunicaciones", items: comunicaciones },
    { id: "reportes", label: "Reportes", items: [{ href: "/reports", label: "Reportes" }] },
    { id: "configuracion", label: "Configuración", items: configuracion },
  ];
  return groups.filter((group) => group.items.length > 0);
}

export function isNavItemActive(pathname: string | null | undefined, href: string): boolean {
  if (!pathname) return false;
  return pathname === href || pathname.startsWith(`${href}/`);
}

// Con varias coincidencias solo gana la mas especifica (p. ej. /declarations/generate no marca otra ruta).
export function activeNavHref(pathname: string | null | undefined, groups: NavGroup[]): string | null {
  const matches = groups.flatMap((g) => g.items).filter((item) => isNavItemActive(pathname, item.href));
  matches.sort((a, b) => b.href.length - a.href.length);
  return matches[0]?.href ?? null;
}

export const MOBILE_NAV_BREAKPOINT_PX = 768;
