"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import { COCKPIT_HREF, canViewCockpit } from "@/lib/cockpitNav";
import { canViewSolVaultNav } from "@/lib/solVaultPermissions";

const NAV_ITEMS = [
  { href: "/clients", label: "Contribuyentes" },
  { href: "/tax-categories", label: "Categorías tributarias" },
  { href: "/tax-schedules", label: "Cronogramas tributarios" },
  { href: "/tax-periods", label: "Periodos tributarios" },
  { href: "/dashboard", label: "Tablero mensual" },
  { href: "/declarations/generate", label: "Generar declaraciones" },
  { href: "/reports", label: "Reportes" },
  { href: "/security", label: "Seguridad" },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user } = useCurrentUser();
  let navItems = canViewCockpit(user) ? [{ href: COCKPIT_HREF, label: "Cockpit mensual" }, ...NAV_ITEMS] : NAV_ITEMS;
  if (hasPermission(user, "task:read")) {
    navItems = [
      ...navItems,
      { href: "/tasks", label: "Tareas" },
      { href: "/agenda", label: "Agenda" },
    ];
  }
  if (hasPermission(user, "user:read")) {
    navItems = [...navItems, { href: "/users", label: "Usuarios" }];
  }
  if (hasPermission(user, "integration:connect")) {
    navItems = [...navItems, { href: "/settings/integrations", label: "Integraciones" }];
  }
  if (hasPermission(user, "communication:draft")) {
    navItems = [...navItems, { href: "/communications", label: "Comunicaciones" }];
  }
  if (canViewSolVaultNav(user)) {
    navItems = [...navItems, { href: "/settings/sol-vault", label: "Bóveda SOL" }];
  }

  return (
    <nav
      style={{
        width: "220px",
        flexShrink: 0,
        borderRight: "1px solid var(--border)",
        padding: "1rem 0.75rem",
        background: "var(--surface)",
      }}
    >
      {navItems.map((item) => {
        const active = pathname?.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            style={{
              display: "block",
              padding: "0.55rem 0.75rem",
              borderRadius: "6px",
              marginBottom: "0.25rem",
              fontSize: "0.92rem",
              fontWeight: active ? 700 : 500,
              background: active ? "var(--border)" : "transparent",
            }}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
