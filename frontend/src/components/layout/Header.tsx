"use client";

import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { logout } from "@/lib/auth";
import type { User } from "@/lib/types/auth";

export function Header({ user }: { user: User | null }) {
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
        padding: "0.85rem 1.5rem",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface)",
      }}
    >
      <strong>Luis IA Contable</strong>
      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
        {user && <span style={{ color: "var(--muted)", fontSize: "0.9rem" }}>{user.full_name}</span>}
        <Button variant="secondary" onClick={handleLogout}>
          Cerrar sesión
        </Button>
      </div>
    </header>
  );
}
