"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { COMMUNICATION_DRAFT_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { ApiError, apiClient } from "@/lib/apiClient";
import { listCommunicationDrafts } from "@/lib/communications";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import type { ClientListResponse } from "@/lib/types/client";
import type { CommunicationDraftOut } from "@/lib/types/communication";

const LIMIT = 20;

const STATUS_OPTIONS = [
  { value: "DRAFT", label: "Borrador" },
  { value: "OPEN_REQUESTED", label: "Se solicitó abrir WhatsApp" },
  { value: "MARKED_SENT", label: "Marcado como enviado por ti" },
  { value: "DISCARDED", label: "Descartado" },
];

export default function CommunicationsPage() {
  return (
    <Suspense fallback={<p>Cargando…</p>}>
      <CommunicationsPageContent />
    </Suspense>
  );
}

function CommunicationsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user } = useCurrentUser();

  const status = searchParams.get("status") ?? "";
  const offset = Number(searchParams.get("offset") ?? "0");

  const [drafts, setDrafts] = useState<CommunicationDraftOut[]>([]);
  const [total, setTotal] = useState(0);
  const [clientNames, setClientNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void apiClient
      .get<ClientListResponse>("/clients?limit=200")
      .then((data) => {
        const map: Record<string, string> = {};
        for (const client of data.items) map[client.id] = `${client.business_name} — ${client.ruc}`;
        setClientNames(map);
      })
      .catch(() => setClientNames({}));
  }, []);

  const updateParam = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(searchParams.toString());
      if (value) next.set(key, value);
      else next.delete(key);
      next.delete("offset");
      router.push(`/communications?${next.toString()}`);
    },
    [router, searchParams],
  );

  const setOffset = useCallback(
    (newOffset: number) => {
      const next = new URLSearchParams(searchParams.toString());
      next.set("offset", String(newOffset));
      router.push(`/communications?${next.toString()}`);
    },
    [router, searchParams],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listCommunicationDrafts({
        status: status || undefined,
        limit: LIMIT,
        offset,
      });
      setDrafts(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar la lista de comunicaciones.");
    } finally {
      setLoading(false);
    }
  }, [status, offset]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: "1.4rem" }}>Comunicaciones</h1>
        {hasPermission(user, "communication:draft") && (
          <Link href="/communications/new">
            <Button type="button">Nuevo borrador</Button>
          </Link>
        )}
      </div>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <Select
          label="Estado"
          placeholder="Todos"
          options={STATUS_OPTIONS}
          value={status}
          onChange={(e) => updateParam("status", e.target.value)}
        />
      </div>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {loading ? (
        <p>Cargando…</p>
      ) : drafts.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>No hay borradores de comunicación para los filtros seleccionados.</p>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th>Estado</th>
                  <th>Creado</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {drafts.map((draft) => (
                  <tr key={draft.id}>
                    <td>{clientNames[draft.client_id] ?? draft.client_id}</td>
                    <td>
                      <StatusSemaphore status={draft.status} map={COMMUNICATION_DRAFT_STATUS_SEMAPHORE} />
                    </td>
                    <td>{new Date(draft.created_at).toLocaleString("es-PE")}</td>
                    <td>
                      <Link href={`/communications/${draft.id}`}>Ver detalle</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        </>
      )}
    </div>
  );
}
