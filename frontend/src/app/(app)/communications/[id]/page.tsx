"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { COMMUNICATION_DRAFT_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { ApiError } from "@/lib/apiClient";
import { isCommunicationDraftOwnedByAsistente } from "@/lib/communicationDraftOwnership";
import { classifyCommunicationMutationError } from "@/lib/communicationMutationError";
import { discardDraft, getCommunicationDraft, markOpenRequested, markSent } from "@/lib/communications";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import type { CommunicationDraftOut } from "@/lib/types/communication";

export default function CommunicationDraftDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useCurrentUser();
  const draftId = params.id;

  const [draft, setDraft] = useState<CommunicationDraftOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // El enlace wa.me solo vive en memoria de este componente -- nunca se
  // guarda en localStorage/sessionStorage, y desaparece al recargar la
  // página (Sección 14 del pedido de Bloque 3.2).
  const [manualWaUrl, setManualWaUrl] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getCommunicationDraft(draftId);
      setDraft(data);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar el borrador.");
    } finally {
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  const handleMutationError = (err: unknown) => {
    const classified = classifyCommunicationMutationError(err);
    if (classified.kind === "VERSION_CONFLICT" || classified.kind === "INVALID_TRANSITION") {
      setNotice(classified.message);
      void reload();
      return;
    }
    setError(classified.message);
  };

  if (loading) return <p>Cargando…</p>;
  if (!draft) return <p style={{ color: "var(--danger)" }}>{error ?? "Borrador no encontrado."}</p>;

  const ownershipBlocked = user?.role === "asistente" && !isCommunicationDraftOwnedByAsistente(user, draft);
  const canOperate = hasPermission(user, "communication:draft") && !ownershipBlocked;
  const canOpenWhatsapp = canOperate && (draft.status === "DRAFT" || draft.status === "OPEN_REQUESTED");
  const canMarkSent = canOperate && draft.status === "OPEN_REQUESTED";
  const canDiscard = canOperate && (draft.status === "DRAFT" || draft.status === "OPEN_REQUESTED");

  const handleOpenWhatsapp = async () => {
    setBusy(true);
    setError(null);
    setManualWaUrl(null);
    try {
      const result = await markOpenRequested(draft.id, draft.version);
      setDraft(result.draft);
      setNotice(result.message);
      const opened = window.open(result.wa_url, "_blank", "noopener,noreferrer");
      if (!opened) {
        // Ventana emergente bloqueada por el navegador: el estado ya quedó
        // en OPEN_REQUESTED (nunca se revierte), se ofrece un enlace manual.
        setManualWaUrl(result.wa_url);
        setNotice("Tu navegador bloqueó la ventana emergente. Usa el enlace manual de abajo.");
      }
    } catch (err) {
      handleMutationError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleMarkSent = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await markSent(draft.id, draft.version);
      setDraft(result.draft);
      setNotice(result.message);
      setManualWaUrl(null);
    } catch (err) {
      handleMutationError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleDiscard = async () => {
    if (!window.confirm("¿Confirmas descartar este borrador?")) return;
    setBusy(true);
    setError(null);
    try {
      const result = await discardDraft(draft.id, draft.version);
      setDraft(result.draft);
      setNotice(result.message);
      setManualWaUrl(null);
    } catch (err) {
      handleMutationError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "700px" }}>
      <Button type="button" variant="secondary" onClick={() => router.push("/communications")}>
        ← Volver a comunicaciones
      </Button>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
      {notice && <p style={{ color: "var(--muted)" }}>{notice}</p>}
      {ownershipBlocked && (
        <p style={{ color: "var(--muted)" }}>
          Este borrador no fue creado por ti — solo puedes verlo, no operarlo.
        </p>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <h1 style={{ fontSize: "1.3rem" }}>Borrador de comunicación</h1>
        <StatusSemaphore status={draft.status} map={COMMUNICATION_DRAFT_STATUS_SEMAPHORE} />
      </div>

      {draft.status === "OPEN_REQUESTED" && (
        <p style={{ color: "var(--muted)" }}>Pendiente de confirmación manual.</p>
      )}

      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "0.4rem 1rem", fontSize: "0.9rem" }}>
        <dt style={{ color: "var(--muted)" }}>Destinatario</dt>
        <dd>{draft.recipient_phone_display}</dd>
        {draft.template_code && (
          <>
            <dt style={{ color: "var(--muted)" }}>Plantilla</dt>
            <dd>
              {draft.template_code} (v{draft.template_version})
            </dd>
          </>
        )}
        <dt style={{ color: "var(--muted)" }}>Creado</dt>
        <dd>{new Date(draft.created_at).toLocaleString("es-PE")}</dd>
      </dl>

      <div style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "1rem", whiteSpace: "pre-wrap" }}>
        {draft.body}
      </div>

      {manualWaUrl && (
        <p>
          <a href={manualWaUrl} target="_blank" rel="noopener noreferrer">
            Abrir enlace de WhatsApp manualmente
          </a>
        </p>
      )}

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        {canOpenWhatsapp && (
          <Button type="button" onClick={handleOpenWhatsapp} disabled={busy}>
            Abrir en WhatsApp
          </Button>
        )}
        {canMarkSent && (
          <Button type="button" variant="secondary" onClick={handleMarkSent} disabled={busy}>
            Marcar como enviado
          </Button>
        )}
        {canDiscard && (
          <Button type="button" variant="danger" onClick={handleDiscard} disabled={busy}>
            Descartar
          </Button>
        )}
      </div>
    </div>
  );
}
