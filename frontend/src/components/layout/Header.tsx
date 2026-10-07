"use client";

import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { logout } from "@/lib/auth";
import type { User } from "@/lib/types/auth";

interface HeaderProps {
  user: User | null;
  navOpen: boolean;
  onToggleNav: () => void;
}

export function Header({ user, navOpen, onToggleNav }: HeaderProps) {
  const router = useRouter();

  async function handleLogout() {
    try {
      await logout();
    } finally {
      router.push("/login");
    }
  }

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "0.75rem",
        padding: "0.7rem 1rem",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", minWidth: 0 }}>
        <button
          type="button"
          className="btn btn-secondary app-menu-button"
          aria-label="Menú de navegación"
          aria-controls="app-navigation"
          aria-expanded={navOpen}
          onClick={onToggleNav}
        >
          ☰ Menú
        </button>
        <strong style={{ whiteSpace: "nowrap" }}>Luis IA Contable</strong>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", minWidth: 0 }}>
        {user && (
          <span
            style={{ color: "var(--muted)", fontSize: "0.9rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {user.full_name}
          </span>
        )}
        <Button variant="secondary" onClick={handleLogout}>
          Cerrar sesión
        </Button>
      </div>
    </header>
  );
}
