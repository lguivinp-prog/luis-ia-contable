"use client";

import Link from "next/link";

import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";

export default function IntegrationsPage() {
  const { user } = useCurrentUser();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "700px" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Integraciones</h1>
      {hasPermission(user, "integration:connect") ? (
        <Link href="/settings/integrations/google" className="btn btn-secondary" style={{ width: "fit-content" }}>
          Google
        </Link>
      ) : (
        <p>No tienes permiso para gestionar integraciones.</p>
      )}
    </div>
  );
}
