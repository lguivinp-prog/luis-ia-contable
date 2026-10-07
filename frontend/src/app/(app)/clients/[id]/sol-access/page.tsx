"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useCurrentUser } from "@/lib/currentUserContext";
import { cancelSolSession, createSolSession, fetchClientSolSession, fetchSolAgents } from "@/lib/solAccess";
import {
  availableActions,
  classifyStartFailure,
  createStartGuard,
  describeCancelError,
  describeConnectionTrouble,
  describeSessionState,
  describeStartError,
  describeTerminalReason,
  evaluatePreflight,
  nextPollDelayMs,
  operationalHint,
  shouldPoll,
  WAITING_FOR_USER_INSTRUCTIONS,
} from "@/lib/solAccessModel";
import { canCancelSolSession, canStartSolSession, canViewSupervisedSolAccess } from "@/lib/solAccessPermissions";
import { fetchClientSolCredentialStatus, fetchSolVaultStatus, unlockSolVault } from "@/lib/solVault";
import type { SolAccessSessionOut, SolAgentsOut } from "@/lib/types/solAccess";
import type { SolCredentialStatusOut, SolVaultStatusOut } from "@/lib/types/solVault";

function detailOf(err: unknown): unknown {
  return (err as { detail?: unknown } | undefined)?.detail;
}

export default function ClientSolAccessPage() {
  const params = useParams<{ id: string }>();
  const clientId = params.id;
  const { user } = useCurrentUser();
  const canView = canViewSupervisedSolAccess(user);
  const canStart = canStartSolSession(user);
  const canCancel = canCancelSolSession(user);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [session, setSession] = useState<SolAccessSessionOut | null>(null);
  const [credential, setCredential] = useState<SolCredentialStatusOut | null>(null);
  const [vault, setVault] = useState<SolVaultStatusOut | null>(null);
  const [agents, setAgents] = useState<SolAgentsOut | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [failures, setFailures] = useState(0);

  // La clave de idempotencia vive solo en memoria de este componente (nunca en almacenamiento del navegador).
  const guardRef = useRef(createStartGuard(() => crypto.randomUUID()));

  // Contraseña de la bóveda: solo en estado del componente; se borra al terminar la solicitud.
  const [vaultPassword, setVaultPassword] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const [unlockError, setUnlockError] = useState<string | null>(null);

  const loadContext = useCallback(async () => {
    const [cred, vlt, agt] = await Promise.allSettled([
      fetchClientSolCredentialStatus(clientId),
      fetchSolVaultStatus(),
      fetchSolAgents(),
    ]);
    setCredential(cred.status === "fulfilled" ? cred.value : null);
    setVault(vlt.status === "fulfilled" ? vlt.value : null);
    setAgents(agt.status === "fulfilled" ? agt.value : null);
  }, [clientId]);

  // Carga inicial: restaura la sesion activa desde el backend (la UI no recuerda nada por si misma).
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const current = await fetchClientSolSession(clientId);
        if (!alive) return;
        setSession(current);
        await loadContext();
      } catch {
        if (alive) setLoadError("No se pudo cargar el estado del acceso SOL.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [clientId, loadContext]);

  // Sondeo SOLO al backend; se detiene al llegar a un estado terminal.
  const sessionId = session?.id ?? null;
  const polling = shouldPoll(session);
  useEffect(() => {
    if (!polling || !sessionId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let misses = 0;
    const tick = async () => {
      try {
        const current = await fetchClientSolSession(clientId);
        if (!alive) return;
        misses = 0;
        setFailures(0);
        setSession(current);
        if (!shouldPoll(current)) {
          void loadContext();
          return;
        }
      } catch {
        if (!alive) return;
        misses += 1;
        setFailures(misses);
      }
      timer = setTimeout(() => void tick(), nextPollDelayMs(misses));
    };
    timer = setTimeout(() => void tick(), nextPollDelayMs(0));
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [polling, sessionId, clientId, loadContext]);

  async function handleUnlock(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (unlocking || !vaultPassword) return;
    setUnlocking(true);
    setUnlockError(null);
    try {
      await unlockSolVault(vaultPassword);
      await loadContext();
    } catch {
      setUnlockError("No se pudo desbloquear la bóveda. Verifica la contraseña e inténtalo otra vez.");
    } finally {
      setVaultPassword("");
      setUnlocking(false);
    }
  }

  async function handleStart() {
    const key = guardRef.current.begin();
    if (key === null) return;
    setStarting(true);
    setActionError(null);
    try {
      const created = await createSolSession(clientId, key);
      guardRef.current.finish("created");
      setSession(created);
    } catch (err) {
      const detail = detailOf(err);
      guardRef.current.finish(classifyStartFailure(detail));
      setActionError(describeStartError(detail));
      // Estado autoritativo: el backend pudo haber creado la sesion aunque la respuesta se perdiera.
      try {
        setSession(await fetchClientSolSession(clientId));
      } catch {
        /* se mantiene el mensaje de error */
      }
      void loadContext();
    } finally {
      setStarting(false);
    }
  }

  async function handleCancel() {
    if (!session || cancelling) return;
    if (!window.confirm("¿Cancelar el acceso SOL en curso?")) return;
    setCancelling(true);
    setActionError(null);
    try {
      setSession(await cancelSolSession(session.id));
    } catch (err) {
      setActionError(describeCancelError(detailOf(err)));
      try {
        setSession(await fetchClientSolSession(clientId));
      } catch {
        /* se mantiene el mensaje de error */
      }
    } finally {
      setCancelling(false);
    }
  }

  if (loading) return <p>Cargando…</p>;
  if (!canView) return <p>No tienes permiso para ver el acceso SOL supervisado.</p>;

  const preflight = evaluatePreflight({ canStart, credential, vault, agents, session });
  const actions = availableActions(session, canCancel);
  const state = session ? describeSessionState(session.status) : null;
  const reason = session ? describeTerminalReason(session.terminal_reason_code) : null;
  const trouble = describeConnectionTrouble(failures);
  const vaultLocked = vault != null && vault.initialized && vault.status === "ACTIVE" && !vault.unlocked;
  const active = session != null && polling;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "640px" }}>
      <div>
        <h1 style={{ fontSize: "1.4rem" }}>Acceso SOL supervisado</h1>
        <p style={{ color: "var(--muted)", marginTop: "0.35rem" }}>
          Autentica hasta el menú SOL con el agente local. Nunca se muestran usuario, clave ni datos de la sesión.
        </p>
      </div>

      {loadError && (
        <div className="simulated-banner" role="alert" style={{ borderColor: "var(--danger, #c0392b)" }}>
          {loadError}
        </div>
      )}
      {actionError && (
        <div className="simulated-banner" role="alert" style={{ borderColor: "var(--danger, #c0392b)" }}>
          {actionError}
        </div>
      )}

      <section className="surface-card" style={{ padding: "1.25rem" }} aria-labelledby="sol-access-estado">
        <h2 id="sol-access-estado" style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>
          Estado previo
        </h2>
        <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
          {preflight.checks.map((check) => (
            <li key={check.id}>
              <strong>{check.ok ? "✔ Listo" : "✖ Pendiente"}</strong> · {check.label}: {check.detail}
            </li>
          ))}
        </ul>
      </section>

      {vaultLocked && canStart && !active && (
        <section className="surface-card" style={{ padding: "1.25rem" }} aria-labelledby="sol-access-boveda">
          <h2 id="sol-access-boveda" style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>
            Desbloquear bóveda
          </h2>
          <form onSubmit={handleUnlock} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <Input
              id="sol-access-vault-password"
              label="Contraseña de la bóveda"
              type="password"
              autoComplete="off"
              required
              value={vaultPassword}
              onChange={(e) => setVaultPassword(e.target.value)}
            />
            {unlockError && <div role="alert">{unlockError}</div>}
            <Button type="submit" disabled={unlocking || !vaultPassword}>
              {unlocking ? "Desbloqueando…" : "Desbloquear bóveda"}
            </Button>
          </form>
        </section>
      )}

      {!session || !active ? (
        canStart && (
          <section className="surface-card" style={{ padding: "1.25rem" }}>
            <Button type="button" onClick={handleStart} disabled={!preflight.canStart || starting}>
              {starting ? "Iniciando…" : "Iniciar acceso SOL"}
            </Button>
            {!preflight.canStart && (
              <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>
                Completa los requisitos pendientes para habilitar el inicio.
              </p>
            )}
          </section>
        )
      ) : null}

      {session && state && (
        <section
          className="surface-card"
          style={{ padding: "1.25rem" }}
          role="status"
          aria-live="polite"
          aria-labelledby="sol-access-sesion"
        >
          <h2 id="sol-access-sesion" style={{ fontSize: "1.05rem", marginBottom: "0.5rem" }}>
            Sesión de acceso
          </h2>
          <p>
            <strong>Estado:</strong> {state.label}
          </p>
          <p style={{ marginTop: "0.35rem" }}>{state.description}</p>
          {session.status === "WAITING_FOR_USER" && (
            <div className="simulated-banner" style={{ marginTop: "0.75rem", fontWeight: 600 }}>
              <p>Requiere tu intervención</p>
              <ul style={{ marginTop: "0.35rem", paddingLeft: "1.2rem" }}>
                {WAITING_FOR_USER_INSTRUCTIONS.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          )}
          {operationalHint(session, agents) && (
            <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>{operationalHint(session, agents)}</p>
          )}
          {trouble && <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>{trouble}</p>}
          {reason && session.status !== "SUCCEEDED" && (
            <p style={{ marginTop: "0.5rem" }}>
              {reason.message}
              {reason.supportCode ? <span style={{ color: "var(--muted)" }}> (código: {reason.supportCode})</span> : null}
            </p>
          )}
          {actions.includes("cancel") && (
            <Button type="button" variant="danger" onClick={handleCancel} disabled={cancelling} style={{ marginTop: "0.75rem" }}>
              {cancelling ? "Cancelando…" : "Cancelar acceso"}
            </Button>
          )}
        </section>
      )}
    </div>
  );
}
