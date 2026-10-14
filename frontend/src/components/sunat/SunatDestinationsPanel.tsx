"use client";

// Accesos SUNAT del contribuyente (M3): una tarjeta por destino con su estado REAL (catalogo del backend + sesion actual). Abrir un destino
// es un evento de ACCESO: la automatizacion solo autentica, navega por la secuencia validada y entrega el navegador; nunca presenta, paga,
// declara ni marca nada como cumplido. Un destino no validado informa su estado y NO ofrece accion (la alternativa es «Abrir Menú SOL»).
// Se usa en el workspace mensual (con el periodo como contexto de auditoria) y puede reutilizarse en otras pantallas del cliente.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { useCurrentUser } from "@/lib/currentUserContext";
import { CredentialProfilePicker } from "@/components/sunat/CredentialProfilePicker";
import { createSolSession, fetchClientSolSession, fetchCredentialDestinations, fetchSolDestinations } from "@/lib/solAccess";
import { fetchClientSolCredentials } from "@/lib/solVault";
import { canReadSolCredential } from "@/lib/solVaultPermissions";
import {
  classifyStartFailure,
  createStartGuard,
  describeDestinationStates,
  describeDiagnostic,
  describeStartError,
  destinationUiState,
  nextPollDelayMs,
  resolveSelectedProfile,
  selectionRequiredFor,
  shouldPoll,
} from "@/lib/solAccessModel";
import { canStartSolSession, canViewSupervisedSolAccess } from "@/lib/solAccessPermissions";
import type { SolAccessSessionOut, SolCredentialPermissions, SolDestination, SolDestinationInfo } from "@/lib/types/solAccess";
import type { SolCredentialStatusOut } from "@/lib/types/solVault";

const TONE: Record<string, string> = {
  AVAILABLE: "var(--success, #2e7d32)",
  NOT_VALIDATED: "var(--muted)",
  IN_PROGRESS: "var(--info, #1565c0)",
  NEEDS_HUMAN: "var(--warning, #b26a00)",
  HANDED_OVER: "var(--info, #1565c0)",
  FINISHED: "var(--success, #2e7d32)",
  FAILED: "var(--danger, #c0392b)",
  NOT_AUTHORIZED: "var(--danger, #c0392b)",
  NEEDS_PROOF: "var(--warning, #b26a00)",
  CANCELLED: "var(--muted)",
};

// Destinos funcionalmente identicos o contenidos: se explican en la tarjeta del destino real en vez de duplicar el acceso.
const ALIAS_NOTE: Partial<Record<SolDestination, string>> = {
  MENU_SOL: "Incluye «Trámites y Consultas»: el Menú SOL completo según las autorizaciones de la credencial.",
  DECLARACIONES_Y_PAGOS: "Incluye «Declara Fácil», que está dentro de esta plataforma.",
};

function detailOf(err: unknown): unknown {
  return (err as { detail?: unknown } | undefined)?.detail;
}

interface Props {
  clientId: string;
  originTaxPeriodId?: string;
  title?: string;
}

export function SunatDestinationsPanel({ clientId, originTaxPeriodId, title = "Accesos SUNAT" }: Props) {
  const { user } = useCurrentUser();
  const canView = canViewSupervisedSolAccess(user);
  const canStart = canStartSolSession(user);
  const [catalog, setCatalog] = useState<SolDestinationInfo[]>([]);
  const [profiles, setProfiles] = useState<SolCredentialStatusOut[]>([]);
  const [chosenProfileId, setChosenProfileId] = useState<string | null>(null);
  const [permissions, setPermissions] = useState<SolCredentialPermissions[]>([]);
  const canSeeProfiles = canReadSolCredential(user);
  const [session, setSession] = useState<SolAccessSessionOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<SolDestination | null>(null);
  const guardRef = useRef(createStartGuard(() => crypto.randomUUID()));

  const load = useCallback(async () => {
    try {
      const [dst, current] = await Promise.all([fetchSolDestinations(), fetchClientSolSession(clientId)]);
      setCatalog(dst.catalog ?? []);
      setSession(current);
      if (canSeeProfiles) {
        const [list, perm] = await Promise.allSettled([fetchClientSolCredentials(clientId), fetchCredentialDestinations(clientId)]);
        setProfiles(list.status === "fulfilled" ? list.value : []);
        setPermissions(perm.status === "fulfilled" ? perm.value.credentials : []);
      }
    } catch {
      setError("No se pudo cargar el estado de los accesos SUNAT.");
    }
  }, [clientId, canSeeProfiles]);

  useEffect(() => {
    if (!canView) return;
    void Promise.resolve().then(() => load());
  }, [canView, load]);

  const sessionId = session?.id ?? null;
  const polling = shouldPoll(session);
  useEffect(() => {
    if (!polling || !sessionId) return;
    let alive = true;
    let misses = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const current = await fetchClientSolSession(clientId);
        if (!alive) return;
        misses = 0;
        setSession(current);
        if (!shouldPoll(current)) return;
      } catch {
        if (!alive) return;
        misses += 1;
      }
      timer = setTimeout(() => void tick(), nextPollDelayMs(misses));
    };
    timer = setTimeout(() => void tick(), nextPollDelayMs(0));
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [polling, sessionId, clientId]);

  const selectedProfile = resolveSelectedProfile(profiles, chosenProfileId);
  const needsChoice = selectionRequiredFor(profiles, chosenProfileId);
  const selectedPermissions = permissions.find((c) => c.credential_id === selectedProfile?.id) ?? null;

  async function open(destination: SolDestination) {
    if (!session || session.is_terminal === true) guardRef.current.reset(); // cada intencion nueva usa su PROPIA clave de idempotencia
    const key = guardRef.current.begin();
    if (key === null) return;
    setStarting(destination);
    setError(null);
    try {
      // Con varios perfiles el usuario ELIGE la credencial; el servidor jamas elige ni reintenta con otra.
      const created = await createSolSession(clientId, key, { destination, originTaxPeriodId, credentialId: selectedProfile?.id });
      guardRef.current.finish("created");
      setSession(created);
    } catch (err) {
      guardRef.current.finish(classifyStartFailure(detailOf(err)));
      setError(describeStartError(detailOf(err)));
      void load();
    } finally {
      setStarting(null);
    }
  }

  if (!canView) return null;
  const diagnostic = session && (session.status === "FAILED" || session.status === "WAITING_FOR_USER") ? describeDiagnostic(session.diagnostic_code) : null;

  return (
    <section className="surface-card" style={{ padding: "1.1rem", display: "flex", flexDirection: "column", gap: "0.8rem" }} aria-labelledby="sunat-accesos">
      <div>
        <h2 id="sunat-accesos" style={{ fontSize: "1.05rem" }}>{title}</h2>
        <p style={{ color: "var(--muted)", fontSize: "0.85rem", marginTop: "0.25rem" }}>
          Abre la herramienta en el navegador local con la credencial de la bóveda. Abrirla es solo un acceso: no declara, no presenta, no paga y no
          cambia ningún estado de cumplimiento.
        </p>
      </div>
      <CredentialProfilePicker clientId={clientId} profiles={profiles} chosenId={chosenProfileId} onChoose={setChosenProfileId} disabled={starting !== null} />
      {needsChoice && <p role="status" style={{ fontSize: "0.85rem" }}>Elige primero con qué credencial SOL se abrirá el acceso.</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 230px), 1fr))", gap: "0.7rem" }}>
        {catalog.filter((info) => info.offered !== false).map((info) => {
          const permission = selectedPermissions?.destinations.find((d) => d.destination === info.id);
          const state = destinationUiState(info, session, permission);
          const enabled = state.canOpen && canStart && starting === null && !needsChoice;
          return (
            <div key={info.id} data-testid={`dest-${info.id}`} data-state={state.key} style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.8rem", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
              <strong>{info.label}</strong>
              <span style={{ color: TONE[state.key], fontSize: "0.85rem", fontWeight: 600 }}>{state.label}</span>
              {state.hint && <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>{state.hint}</span>}
              {permission && <span data-testid={`perm-${info.id}`} data-permitted={permission.permitted} style={{ color: "var(--muted)", fontSize: "0.75rem" }}>{describeDestinationStates(permission)}</span>}
              {ALIAS_NOTE[info.id] && <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>{ALIAS_NOTE[info.id]}</span>}
              {info.enabled && state.key !== "NEEDS_PROOF" && (
                <Button type="button" variant="secondary" disabled={!enabled} onClick={() => void open(info.id)}>
                  {starting === info.id ? "Iniciando…" : info.id === "FICHA_RUC" ? "Abrir Ficha RUC (solo ver)" : `Abrir ${info.label}`}
                </Button>
              )}
            </div>
          );
        })}
      </div>
      {diagnostic && <p role="status" style={{ fontSize: "0.85rem" }}>{diagnostic.message}</p>}
      {!canStart && <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>Tu rol puede consultar el estado pero no iniciar accesos SUNAT.</p>}
      <p style={{ fontSize: "0.85rem" }}>
        <Link href={`/clients/${clientId}/sol-access`}>Abrir el Centro SUNAT del cliente</Link> (desbloquear la bóveda, cancelar o ver el detalle de la sesión).
      </p>
    </section>
  );
}
