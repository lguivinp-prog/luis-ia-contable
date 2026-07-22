"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/clients", label: "Contribuyentes" },
  { href: "/tax-categories", label: "Categorías tributarias" },
  { href: "/tax-schedules", label: "Cronogramas tributarios" },
  { href: "/security", label: "Seguridad" },
];

export function Sidebar() {
  const pathname = usePathname();

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
      {NAV_ITEMS.map((item) => {
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
