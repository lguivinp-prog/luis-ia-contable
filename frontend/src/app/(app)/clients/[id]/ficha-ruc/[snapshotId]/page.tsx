"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { FichaReviewPanel } from "@/components/clients/FichaReviewPanel";
import { useCurrentUser } from "@/lib/currentUserContext";
import { ApiError, apiClient } from "@/lib/apiClient";
import { acceptFichaSnapshot, fetchFichaSnapshot, rejectFichaSnapshot } from "@/lib/fichaRuc";
import { type AcceptSelection, describeFichaError } from "@/lib/fichaRucModel";
import { canReviewFicha } from "@/lib/sunatPermissions";
import type { Client } from "@/lib/types/client";
import type { FichaSnapshot } from "@/lib/types/fichaRuc";

export default function FichaReviewPage() {
  const params = useParams<{ id: string; snapshotId: string }>();
  const { user } = useCurrentUser();
  const canReview = canReviewFicha(user);
  const [snapshot, setSnapshot] = useState<FichaSnapshot | null>(null);
  const [client, setClient] = useState<Client | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [snap, cli] = await Promise.all([
        fetchFichaSnapshot(params.id, params.snapshotId),
        apiClient.get<Client>(`/clients/${params.id}`),
      ]);
      setSnapshot(snap);
      setClient(cli);
    } catch (err) {
      setError(err instanceof ApiError ? describeFichaError(err.detail) : "No se pudo cargar la propuesta.");
    }
  }, [params.id, params.snapshotId]);

  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  async function run(action: () => Promise<FichaSnapshot>, success: string) {
    setBusy(true);
    setError(null);
    try {
      setSnapshot(await action());
      setNotice(success);
    } catch (err) {
      setError(err instanceof ApiError ? describeFichaError(err.detail) : "No se pudo completar la operación.");
      await load(); // estado autoritativo ante conflicto/version
    } finally {
      setBusy(false);
    }
  }

  if (error && !snapshot) return <p role="alert" style={{ color: "var(--danger)" }}>{error}</p>;
  if (!snapshot || !client) return <p>Cargando…</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem", maxWidth: "980px" }}>
      <div>
        <Link href={`/clients/${client.id}/sol-access`} style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          ← Volver al acceso SOL del cliente
        </Link>
        <h1 style={{ fontSize: "1.4rem", marginTop: "0.3rem" }}>Revisar Ficha RUC</h1>
        <p style={{ color: "var(--muted)" }}>
          {client.business_name} · RUC {client.ruc}
        </p>
      </div>
      {error && <p role="alert" style={{ color: "var(--danger)" }}>{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!canReview && snapshot.review_status === "PENDING_REVIEW" && (
        <p style={{ color: "var(--muted)" }}>Solo lectura: tu rol no puede aceptar ni rechazar cambios del cliente.</p>
      )}
      <FichaReviewPanel
        snapshot={snapshot}
        readOnly={!canReview}
        allowOverrides={false}
        busy={busy}
        confirmLabel="Aplicar los cambios marcados"
        onConfirm={(selection: AcceptSelection) =>
          void run(() => acceptFichaSnapshot(client.id, snapshot.id, selection.accepted, snapshot.version), "Cambios aplicados al cliente.")
        }
        rejectLabel="Rechazar propuesta"
        onReject={() => void run(() => rejectFichaSnapshot(client.id, snapshot.id, snapshot.version), "Propuesta rechazada: el cliente no cambió.")}
      />
    </div>
  );
}
