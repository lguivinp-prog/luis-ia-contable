"use client";

import { SecureForm } from "@/components/ui/SecureForm";
import { DeliberateInput } from "@/components/ui/DeliberateInput";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { useCurrentUser } from "@/lib/currentUserContext";
import { fetchFichaOverview, listFichaSnapshots } from "@/lib/fichaRuc";
import { describeOfficialRegime, formatDateTime, UNCHANGED_MESSAGE, verificationOutcome } from "@/lib/fichaRucModel";
import {
  cancelSolSession,
  createSolSession,
  fetchCredentialDestinations,
  fetchClientSolSession,
  fetchSolAgents,
  fetchSolDestinations,
} from "@/lib/solAccess";
import {
  availableActions,
  classifyStartFailure,
  createStartGuard,
  describeCancelError,
  DESTINATION_LABEL,
  describeConnectionTrouble,
  describeSession,
  describeStartError,
  describeDestinationStates,
  describeDiagnostic,
  describeTerminalReason,
  destinationUiState,
  evaluatePreflight,
  nextPollDelayMs,
  operationalHint,
  resolveSelectedProfile,
  selectionRequiredFor,
  shouldPoll,
  visibleDestinations,
  WAITING_FOR_USER_INSTRUCTIONS,
} from "@/lib/solAccessModel";
import { canCancelSolSession, canManageAcceptance, canStartSolSession, canViewSupervisedSolAccess } from "@/lib/solAccessPermissions";
import { AcceptancePanel } from "@/components/sunat/AcceptancePanel";
import { canRefreshFicha } from "@/lib/sunatPermissions";
import { fetchClientSolCredentials, fetchSolVaultStatus, unlockSolVault } from "@/lib/solVault";
import { CredentialProfilePicker } from "@/components/sunat/CredentialProfilePicker";
import type { FichaOverview, FichaSnapshot } from "@/lib/types/fichaRuc";
import type { SolAccessSessionOut, SolAgentsOut, SolCredentialPermissions, SolDestination, SolDestinationInfo } from "@/lib/types/solAccess";
import type { SolCredentialStatusOut, SolVaultStatusOut } from "@/lib/types/solVault";

function changedCount(snapshot: FichaSnapshot): number {
  return snapshot.diff.filter((d) => d.changed).length;
}

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
  const canRefresh = canRefreshFicha(user);
  const canAccept = canManageAcceptance(user);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [session, setSession] = useState<SolAccessSessionOut | null>(null);
  const [profiles, setProfiles] = useState<SolCredentialStatusOut[]>([]);
  const [chosenProfileId, setChosenProfileId] = useState<string | null>(null);
  const [permissions, setPermissions] = useState<SolCredentialPermissions[]>([]);
  const [vault, setVault] = useState<SolVaultStatusOut | null>(null);
  const [agents, setAgents] = useState<SolAgentsOut | null>(null);
  const [destinations, setDestinations] = useState<SolDestination[]>([]);
  const [catalog, setCatalog] = useState<SolDestinationInfo[]>([]);
  const [pendingProposal, setPendingProposal] = useState<FichaSnapshot | null>(null);
  const [overview, setOverview] = useState<FichaOverview | null>(null);
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
    const [cred, vlt, agt, dst, perm] = await Promise.allSettled([
      fetchClientSolCredentials(clientId),
      fetchSolVaultStatus(),
      fetchSolAgents(),
      fetchSolDestinations(),
      fetchCredentialDestinations(clientId),
    ]);
    setProfiles(cred.status === "fulfilled" ? cred.value : []);
    setPermissions(perm.status === "fulfilled" ? perm.value.credentials : []);
    setVault(vlt.status === "fulfilled" ? vlt.value : null);
    setAgents(agt.status === "fulfilled" ? agt.value : null);
    // Solo destinos habilitados por el servidor Y conocidos por la UI (los no validados nunca se muestran).
    setDestinations(dst.status === "fulfilled" ? visibleDestinations(dst.value.destinations) : []);
    setCatalog(dst.status === "fulfilled" ? (dst.value.catalog ?? []) : []);
  }, [clientId]);

  // Propuesta pendiente de la Ficha RUC (para ofrecer la revision humana). Solo con permiso de edicion.
  const loadProposal = useCallback(async () => {
    if (!canRefresh) return;
    try {
      const [snaps, summary] = await Promise.all([listFichaSnapshots(clientId), fetchFichaOverview(clientId)]);
      setPendingProposal(snaps.find((snap) => snap.review_status === "PENDING_REVIEW") ?? null);
      setOverview(summary);
    } catch {
      setPendingProposal(null);
    }
  }, [clientId, canRefresh]);

  const sessionStatus = session?.status;
  useEffect(() => {
    void Promise.resolve().then(() => loadProposal());
  }, [loadProposal, sessionStatus]);

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

  const selectedProfile = resolveSelectedProfile(profiles, chosenProfileId);

  async function handleStart(destination?: SolDestination, extractFicha = false) {
    // Cada intencion nueva (otra sesion tras una terminal / otro destino) usa su PROPIA clave de idempotencia.
    if (!session || session.is_terminal === true) guardRef.current.reset();
    const key = guardRef.current.begin();
    if (key === null) return;
    setStarting(true);
    setActionError(null);
    try {
      // Con varios perfiles el usuario ELIGE la credencial; el servidor jamas elige ni reintenta con otra.
      const created = await createSolSession(clientId, key, { destination, extractFicha, credentialId: selectedProfile?.id });
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

  const preflight = evaluatePreflight({ canStart, credential: selectedProfile, vault, agents, session, selectionRequired: selectionRequiredFor(profiles, chosenProfileId) });
  const selectedPermissions = permissions.find((c) => c.credential_id === selectedProfile?.id) ?? null;
  // Un destino con estructura validada solo se ofrece si ESTE perfil lo tiene disponible (prueba propia): la prueba de una credencial no se extrapola.
  const permissionFor = (id: SolDestination) => selectedPermissions?.destinations.find((d) => d.destination === id) ?? null;
  const openable = (id: SolDestination) => {
    const info = catalog.find((c) => c.id === id);
    if (!info?.requires_profile_proof) return true;
    return permissionFor(id)?.available === true;
  };
  const actions = availableActions(session, canCancel);
  const state = session ? describeSession(session) : null;
  const reason = session ? describeTerminalReason(session.terminal_reason_code) : null;
  const diagnostic = session && (session.status === "FAILED" || session.status === "WAITING_FOR_USER") ? describeDiagnostic(session.diagnostic_code) : null;
  const trouble = describeConnectionTrouble(failures);
  const vaultLocked = vault != null && vault.initialized && vault.status === "ACTIVE" && !vault.unlocked;
  const active = session != null && polling;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "640px" }}>
      <div>
        <h1 style={{ fontSize: "1.4rem" }}>Centro SUNAT del cliente</h1>
        <p style={{ color: "var(--muted)", marginTop: "0.35rem" }}>
          Acceso SOL supervisado: abre un destino validado de SOL con el agente local y lo deja en tus manos. Nunca se muestran usuario, clave ni datos de la sesión.
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
          <SecureForm onSubmit={handleUnlock} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <DeliberateInput secret
              id="sol-access-vault-password"
              label="Contraseña de la bóveda"
              required
              value={vaultPassword}
              onChange={(e) => setVaultPassword(e.target.value)}
            />
            {unlockError && <div role="alert">{unlockError}</div>}
            <Button type="submit" disabled={unlocking || !vaultPassword}>
              {unlocking ? "Desbloqueando…" : "Desbloquear bóveda"}
            </Button>
          </SecureForm>
        </section>
      )}

      {overview && (
        <section className="surface-card" style={{ padding: "1.25rem" }} aria-labelledby="sol-access-info">
          <h2 id="sol-access-info" style={{ fontSize: "1.05rem", marginBottom: "0.5rem" }}>
            Información SUNAT guardada
          </h2>
          <p>
            <strong>Régimen según SUNAT:</strong>{" "}
            {describeOfficialRegime(overview).confirmed
              ? `${describeOfficialRegime(overview).text} (al ${formatDateTime(describeOfficialRegime(overview).asOf)})`
              : describeOfficialRegime(overview).text}
          </p>
          <p style={{ color: "var(--muted)", marginTop: "0.3rem" }}>
            {overview.latest_accepted
              ? `Última Ficha RUC aceptada: consultada el ${formatDateTime(overview.latest_accepted.retrieved_at)}. Puedes revisarla en la ficha del cliente sin abrir SUNAT.`
              : "Aún no hay una Ficha RUC aceptada."}
          </p>
          {overview.pending_snapshot_id && (
            <p role="status" style={{ marginTop: "0.4rem", fontWeight: 600 }}>
              Hay cambios de la Ficha RUC sin revisar.{" "}
              {canRefresh && (
                <Link href={`/clients/${clientId}/ficha-ruc/${overview.pending_snapshot_id}`}>Revisar la propuesta pendiente</Link>
              )}
            </p>
          )}
          {overview.last_unchanged_verification_at && !overview.pending_snapshot_id && (
            <p style={{ color: "var(--muted)", marginTop: "0.3rem" }}>
              Última verificación sin cambios: {formatDateTime(overview.last_unchanged_verification_at)}.
            </p>
          )}
          <p style={{ marginTop: "0.4rem" }}>
            <Link href={`/clients/${clientId}`}>Ver Información SUNAT del cliente</Link>
          </p>
        </section>
      )}

      {!session || !active ? (
        canStart && (
          <section className="surface-card" style={{ padding: "1.25rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div>
              <h2 style={{ fontSize: "1.05rem", marginBottom: "0.4rem" }}>Abrir en SUNAT (solo ver)</h2>
              <CredentialProfilePicker clientId={clientId} profiles={profiles} chosenId={chosenProfileId} onChoose={setChosenProfileId} disabled={starting} />
              <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
                {destinations.filter(openable).map((destination) => (
                  <Button
                    key={destination}
                    type="button"
                    onClick={() => void handleStart(destination)}
                    disabled={!preflight.canStart || starting}
                  >
                    Abrir {DESTINATION_LABEL[destination]}
                  </Button>
                ))}
                <Button type="button" variant="secondary" onClick={() => void handleStart()} disabled={!preflight.canStart || starting}>
                  {starting ? "Iniciando…" : "Solo autenticar (hasta el menú)"}
                </Button>
              </div>
              <p style={{ color: "var(--muted)", marginTop: "0.4rem", fontSize: "0.85rem" }}>
                Abre el destino en el navegador local y lo deja en tus manos. No lee ni guarda datos.
              </p>
            </div>
            {selectedPermissions && (
              <details data-testid="profile-destination-states">
                <summary style={{ cursor: "pointer", fontWeight: 600 }}>Estado por destino de «{selectedPermissions.profile_name}»</summary>
                <ul style={{ listStyle: "none", padding: 0, margin: "0.4rem 0 0", display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                  {selectedPermissions.destinations.map((d) => (
                    <li key={d.destination} data-testid={`perm-${d.destination}`} data-permitted={d.permitted} style={{ fontSize: "0.85rem" }}>
                      <strong>{DESTINATION_LABEL[d.destination]}</strong>: {describeDestinationStates(d)}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {catalog.some((d) => d.offered !== false && (!d.enabled || !openable(d.id))) && (
              <div data-testid="unvalidated-destinations">
                <h2 style={{ fontSize: "1.05rem", marginBottom: "0.4rem" }}>Otros destinos de SUNAT</h2>
                <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                  {catalog
                    .filter((d) => d.offered !== false && (!d.enabled || !openable(d.id)))
                    .map((d) => {
                      const state = destinationUiState(d, session, permissionFor(d.id));
                      return (
                        <li key={d.id} data-testid={`dest-${d.id}`} data-state={state.key} data-evidence={d.evidence ?? "NONE"}>
                          <strong>{d.label}</strong> · {state.label}
                          {d.open_only ? " · solo apertura supervisada" : d.evidence === "ARRIVAL_ONLY" ? " · falta la ruta de menú" : ""}
                        </li>
                      );
                    })}
                </ul>
                <p style={{ color: "var(--muted)", marginTop: "0.4rem", fontSize: "0.85rem" }}>
                  Estos destinos no se abren automáticamente: o no tienen evidencia validada (Generación de Registros se abre a mano desde el Menú SOL), o el perfil
                  elegido todavía no superó su propia prueba real. Usa «Abrir Menú SOL» y navega tú dentro de tus permisos, o pide al propietario que autorice una prueba.
                </p>
              </div>
            )}
            {canAccept && (
              <AcceptancePanel
                clientId={clientId}
                profiles={profiles}
                catalog={catalog}
                session={session}
                starting={starting}
                canOpen={preflight.canStart || (vault?.unlocked === true && agents?.agents.some((a) => a.available) === true)}
                onSession={setSession}
              />
            )}
            {canRefresh && destinations.includes("FICHA_RUC") && (
              <div>
                <h2 style={{ fontSize: "1.05rem", marginBottom: "0.4rem" }}>Actualizar datos desde Ficha RUC</h2>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => void handleStart("FICHA_RUC", true)}
                  disabled={!preflight.canStart || starting}
                >
                  Actualizar datos desde Ficha RUC
                </Button>
                <p style={{ color: "var(--muted)", marginTop: "0.4rem", fontSize: "0.85rem" }}>
                  Lee la Ficha RUC y la compara con la última guardada. Verás cada cambio por separado y decides cuál aceptar; si no hay
                  cambios, se indica «{UNCHANGED_MESSAGE}». Los datos internos (contactos, honorarios, notas) no se tocan.
                </p>
              </div>
            )}
            <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
              Se inicia sesión con la credencial guardada en la bóveda; si SUNAT muestra un aviso de contacto, lo resuelves tú en la
              ventana del navegador (pulsando «Finalizar» cuando corresponda). Nada se envía, presenta, paga ni modifica en SUNAT.
            </p>
            {!preflight.canStart && (
              <p style={{ color: "var(--muted)" }}>Completa los requisitos pendientes para habilitar el inicio.</p>
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
              {diagnostic && <p data-testid="sol-waiting-reason" style={{ marginTop: "0.35rem", fontWeight: 400 }}>{diagnostic.message}</p>}
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
              {diagnostic ? <span data-testid="sol-diagnostic" style={{ display: "block", marginTop: "0.3rem" }}>{diagnostic.message} <span style={{ color: "var(--muted)" }}>(diagnóstico: {diagnostic.code})</span></span> : null}
            </p>
          )}
          {session.status === "SUCCEEDED" && session.destination === "FICHA_RUC" && verificationOutcome(overview, session.created_at) === "UNCHANGED" && (
            <p data-testid="ficha-unchanged" role="status" style={{ marginTop: "0.75rem", fontWeight: 600 }}>
              {UNCHANGED_MESSAGE}
            </p>
          )}
          {pendingProposal && canRefresh && (
            <p style={{ marginTop: "0.75rem" }}>
              <Link href={`/clients/${clientId}/ficha-ruc/${pendingProposal.id}`}>
                Revisar la propuesta de la Ficha RUC ({changedCount(pendingProposal)} {changedCount(pendingProposal) === 1 ? "cambio" : "cambios"})
              </Link>
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
