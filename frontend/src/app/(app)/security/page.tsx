"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Table } from "@/components/ui/Table";
import { listSessions, revokeOtherSessions } from "@/lib/auth";
import type { SessionInfo } from "@/lib/types/auth";

export default function SecurityPage() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    const data = await listSessions();
    setSessions(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleRevokeOthers() {
    setMessage(null);
    const result = await revokeOtherSessions();
    setMessage(`Se cerraron ${result.revoked_count} sesión(es).`);
    await reload();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "900px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: "1.4rem" }}>Seguridad — Sesiones activas</h1>
        <Button type="button" variant="secondary" onClick={handleRevokeOthers}>
          Cerrar otras sesiones
        </Button>
      </div>

      {message && <div className="simulated-banner">{message}</div>}

      {loading ? (
        <p>Cargando…</p>
      ) : (
        <Table
          rows={sessions}
          rowKey={(s) => s.id}
          emptyMessage="No hay sesiones activas."
          columns={[
            {
              key: "current",
              header: "",
              render: (s) => (s.is_current ? <span className="badge">Esta sesión</span> : ""),
            },
            {
              key: "created_at",
              header: "Inicio",
              render: (s) => new Date(s.created_at).toLocaleString("es-PE"),
            },
            {
              key: "last_seen_at",
              header: "Última actividad",
              render: (s) => new Date(s.last_seen_at).toLocaleString("es-PE"),
            },
            {
              key: "expires_at",
              header: "Expira",
              render: (s) => new Date(s.expires_at).toLocaleString("es-PE"),
            },
            {
              key: "ip_address",
              header: "IP",
              render: (s) => s.ip_address ?? "—",
            },
            {
              key: "user_agent",
              header: "Dispositivo",
              render: (s) => s.user_agent ?? "—",
            },
          ]}
        />
      )}
    </div>
  );
}
