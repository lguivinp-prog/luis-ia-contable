"use client";

import { SecureForm } from "@/components/ui/SecureForm";
import { DeliberateInput } from "@/components/ui/DeliberateInput";
// Alta ASISTIDA POR SUNAT: RUC + clave SOL -> boveda -> sesion supervisada hasta la Ficha RUC -> propuesta -> REVISION
// HUMANA -> cliente finalizado. Reglas de secretos: usuario/clave SOL y contraseña de la boveda viven SOLO en estado del
// componente, se borran en cuanto la solicitud termina y NUNCA van a almacenamiento del navegador, URL ni query string.
// En la URL solo viaja el id (no secreto) del alta, para poder retomar tras refrescar.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { FichaReviewPanel } from "@/components/clients/FichaReviewPanel";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { ApiError } from "@/lib/apiClient";
import {
  cancelOnboarding,
  completeOnboarding,
  fetchFichaSnapshot,
  fetchOnboarding,
  rejectFichaSnapshot,
  startOnboarding,
} from "@/lib/fichaRuc";
import {
  type AcceptSelection,
  RUC_CHECK_MESSAGE,
  canRetryWithoutSecrets,
  checkOnboardingRuc,
  deriveOnboardingStep,
  describeFichaError,
  onboardingResumeQuery,
} from "@/lib/fichaRucModel";
import { cancelSolSession, createSolSession, fetchClientSolSession, fetchSolAgents } from "@/lib/solAccess";
import {
  createStartGuard,
  describeSession,
  describeStartError,
  describeDiagnostic,
  describeTerminalReason,
  nextPollDelayMs,
  shouldPoll,
} from "@/lib/solAccessModel";
import { createClientSolCredential, fetchSolVaultStatus, unlockSolVault } from "@/lib/solVault";
import { describeSolVaultError } from "@/lib/solVaultErrors";
import type { FichaSnapshot, Onboarding } from "@/lib/types/fichaRuc";
import type { SolAccessSessionOut, SolAgentsOut } from "@/lib/types/solAccess";
import type { SolCredentialKind, SolVaultStatusOut } from "@/lib/types/solVault";

const KIND_OPTIONS = [
  { value: "SECONDARY", label: "Secundaria (clave SOL propia del despacho)" },
  { value: "PRIMARY_OWN_RUC", label: "Principal (RUC propio del contribuyente)" },
  { value: "PRIMARY_EXCEPTIONAL", label: "Principal (excepción autorizada)" },
];
const DEFAULT_BASIS = "Alta asistida autorizada por el titular de la clave SOL";

function detailOf(err: unknown): unknown {
  return err instanceof ApiError ? err.detail : undefined;
}

const GENERIC_ERROR = "No se pudo completar la operación. Inténtalo otra vez.";

// Los errores pueden venir de la API de alta (ficha), de sesiones SOL o de la boveda: se usa el primer mensaje especifico.
function describeAnyError(detail: unknown): string {
  for (const describe of [describeFichaError, describeStartError, describeSolVaultError]) {
    const message = describe(detail);
    if (message !== GENERIC_ERROR) return message;
  }
  return GENERIC_ERROR;
}

export function AssistedOnboarding() {
  const router = useRouter();
  const search = useSearchParams();
  const resumeId = search.get("onboarding");

  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);
  const [session, setSession] = useState<SolAccessSessionOut | null>(null);
  const [snapshot, setSnapshot] = useState<FichaSnapshot | null>(null);
  const [vault, setVault] = useState<SolVaultStatusOut | null>(null);
  const [agents, setAgents] = useState<SolAgentsOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [activate, setActivate] = useState(false);
  const [loading, setLoading] = useState(Boolean(resumeId));

  // Secretos: SOLO estado transitorio del componente.
  const [ruc, setRuc] = useState("");
  const [solUser, setSolUser] = useState("");
  const [solPassword, setSolPassword] = useState("");
  const [vaultPassword, setVaultPassword] = useState("");
  const [retryVaultPassword, setRetryVaultPassword] = useState("");
  const [kind, setKind] = useState<SolCredentialKind>("SECONDARY");
  const [basis, setBasis] = useState(DEFAULT_BASIS);
  const guard = useRef(createStartGuard(() => crypto.randomUUID()));

  const step = deriveOnboardingStep(onboarding, session);
  const clientId = onboarding?.client_id ?? null;

  const loadPreflight = useCallback(async () => {
    const [v, a] = await Promise.allSettled([fetchSolVaultStatus(), fetchSolAgents()]);
    setVault(v.status === "fulfilled" ? v.value : null);
    setAgents(a.status === "fulfilled" ? a.value : null);
  }, []);

  const refresh = useCallback(
    async (id: string) => {
      const current = await fetchOnboarding(id);
      setOnboarding(current);
      let latest: SolAccessSessionOut | null = null;
      try {
        latest = await fetchClientSolSession(current.client_id);
      } catch {
        latest = null;
      }
      setSession(latest);
      if (current.status === "AWAITING_REVIEW" && current.snapshot_id) {
        setSnapshot(await fetchFichaSnapshot(current.client_id, current.snapshot_id));
      } else {
        setSnapshot(null);
      }
    },
    [],
  );

  useEffect(() => {
    void Promise.resolve().then(() => loadPreflight());
  }, [loadPreflight]);

  // Retomar tras refrescar: el estado viene del backend (los secretos NO se recuperan, por diseño).
  useEffect(() => {
    if (!resumeId) return;
    let alive = true;
    void (async () => {
      try {
        await refresh(resumeId);
      } catch (err) {
        if (alive) setError(describeFichaError(detailOf(err)));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [resumeId, refresh]);

  // Sondeo SOLO al backend mientras la sesion de extraccion esta activa.
  const onboardingId = onboarding?.id ?? null;
  const polling = step === "waiting" && shouldPoll(session);
  useEffect(() => {
    if (!polling || !onboardingId) return;
    let alive = true;
    let misses = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        await refresh(onboardingId);
        misses = 0;
      } catch {
        misses += 1;
      }
      if (alive) timer = setTimeout(() => void tick(), nextPollDelayMs(misses));
    };
    timer = setTimeout(() => void tick(), nextPollDelayMs(0));
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [polling, onboardingId, refresh]);

  const rucCheck = checkOnboardingRuc(ruc);
  const vaultLocked = vault != null && vault.initialized && vault.status === "ACTIVE" && !vault.unlocked;
  const vaultReady = vault != null && vault.initialized && vault.status === "ACTIVE";
  const agentReady = agents != null && agents.broker_enabled && agents.agents.some((a) => a.available);

  async function startExtractionSession(targetClientId: string): Promise<SolAccessSessionOut> {
    guard.current.reset(); // cada intencion de extraccion usa su propia clave
    const key = guard.current.begin();
    if (key === null) throw new Error("in_flight");
    try {
      const created = await createSolSession(targetClientId, key, { destination: "FICHA_RUC", extractFicha: true });
      guard.current.finish("created");
      return created;
    } catch (err) {
      guard.current.finish("rejected");
      throw err;
    }
  }

  async function handleStart(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (rucCheck !== "ok") {
      setError(RUC_CHECK_MESSAGE[rucCheck]);
      return;
    }
    setBusy(true);
    setError(null);
    let created: Onboarding | null = null;
    try {
      if (vaultLocked) {
        try {
          await unlockSolVault(vaultPassword);
        } finally {
          setVaultPassword("");
        }
      }
      created = await startOnboarding(ruc);
      setOnboarding(created);
      router.replace(`/clients/new${onboardingResumeQuery(created.id)}`);
      try {
        await createClientSolCredential(created.client_id, {
          credential_kind: kind,
          sol_username: solUser,
          sol_password: solPassword,
          authorization_basis: basis,
        });
      } finally {
        setSolUser("");
        setSolPassword("");
      }
      setSession(await startExtractionSession(created.client_id));
    } catch (err) {
      const detail = detailOf(err);
      if (created && !session) {
        // La credencial no se pudo guardar: nada queda a medias (alta cancelada, sin credencial huerfana).
        const stored = await credentialWasStored(created.client_id);
        if (!stored) {
          try {
            await cancelOnboarding(created.id);
          } catch {
            /* el backend mantiene la alta; el usuario puede cancelarla */
          }
          setOnboarding(null);
          router.replace("/clients/new");
        }
      }
      setError(err instanceof ApiError ? describeAnyError(detail) : "No se pudo iniciar el alta asistida.");
    } finally {
      setBusy(false);
      void loadPreflight();
    }
  }

  async function credentialWasStored(id: string): Promise<boolean> {
    try {
      const { fetchClientSolCredentialStatus } = await import("@/lib/solVault");
      const status = await fetchClientSolCredentialStatus(id);
      return status != null && status.status !== "DISABLED";
    } catch {
      return false;
    }
  }

  async function handleRetry() {
    if (!clientId || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (vaultLocked) {
        // la bóveda se bloquea al reiniciar la aplicación: se desbloquea aquí, sin volver a escribir las claves SOL
        try {
          await unlockSolVault(retryVaultPassword);
        } finally {
          setRetryVaultPassword("");
        }
      }
      setSession(await startExtractionSession(clientId));
      if (onboarding) await refresh(onboarding.id);
    } catch (err) {
      setError(describeStartError(detailOf(err)));
    } finally {
      setBusy(false);
      void loadPreflight();
    }
  }

  async function handleCancelSession() {
    if (!session || busy) return;
    setBusy(true);
    try {
      setSession(await cancelSolSession(session.id));
    } catch (err) {
      setError(describeStartError(detailOf(err)));
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(selection: AcceptSelection) {
    if (!onboarding || busy) return;
    setBusy(true);
    setError(null);
    try {
      setOnboarding(
        await completeOnboarding(onboarding.id, {
          accepted_fields: selection.accepted,
          overrides: selection.overrides,
          activate,
          expected_version: onboarding.version,
        }),
      );
    } catch (err) {
      setError(describeFichaError(detailOf(err)));
      await refresh(onboarding.id).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  async function handleRejectProposal() {
    if (!onboarding || !snapshot || busy) return;
    setBusy(true);
    setError(null);
    try {
      await rejectFichaSnapshot(onboarding.client_id, snapshot.id, snapshot.version);
      await refresh(onboarding.id);
    } catch (err) {
      setError(describeFichaError(detailOf(err)));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancelOnboarding() {
    if (!onboarding || busy) return;
    if (!window.confirm("¿Cancelar el alta? Se deshabilitará la credencial guardada y el cliente provisional quedará inactivo.")) return;
    setBusy(true);
    setError(null);
    try {
      setOnboarding(await cancelOnboarding(onboarding.id));
      setSnapshot(null);
    } catch (err) {
      setError(describeFichaError(detailOf(err)));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p>Cargando…</p>;
  const state = session ? describeSession(session) : null;
  const reason = session ? describeTerminalReason(session.terminal_reason_code) : null;
  const diagnostic = session && (session.status === "FAILED" || session.status === "WAITING_FOR_USER") ? describeDiagnostic(session.diagnostic_code) : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem", maxWidth: "980px" }}>
      <p style={{ color: "var(--muted)" }}>
        Ingresa el RUC y la clave SOL. Se guardan en la bóveda cifrada, se abre SOL con el agente local hasta la Ficha RUC y se te
        muestra una propuesta para que la revises. El cliente solo se finaliza cuando tú confirmas.
      </p>
      {error && (
        <p role="alert" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}

      {step === "credentials" && (
        <SecureForm onSubmit={handleStart} className="surface-card" style={{ padding: "1.25rem", display: "flex", flexDirection: "column", gap: "0.8rem", maxWidth: "560px" }}>
          <Input id="onb-ruc" label="RUC del contribuyente" inputMode="numeric" maxLength={11} autoComplete="off" required value={ruc} onChange={(e) => setRuc(e.target.value.replace(/\D/g, ""))} />
          {ruc.length === 11 && rucCheck !== "ok" && (
            <p role="status" style={{ color: "var(--danger)", fontSize: "0.85rem" }}>
              {RUC_CHECK_MESSAGE[rucCheck]}
            </p>
          )}
          <Select id="onb-kind" label="Tipo de credencial" options={KIND_OPTIONS} value={kind} onChange={(e) => setKind(e.target.value as SolCredentialKind)} />
          <DeliberateInput id="onb-sol-user" label="Usuario SOL" required value={solUser} onChange={(e) => setSolUser(e.target.value)} />
          <DeliberateInput secret id="onb-sol-password" label="Clave SOL" required value={solPassword} onChange={(e) => setSolPassword(e.target.value)} />
          <Input id="onb-basis" label="Base de autorización" required value={basis} onChange={(e) => setBasis(e.target.value)} />
          {vaultLocked && (
            <DeliberateInput secret id="onb-vault-password" label="Contraseña de la bóveda (para desbloquearla)" required value={vaultPassword} onChange={(e) => setVaultPassword(e.target.value)} />
          )}
          <ul style={{ listStyle: "none", padding: 0, color: "var(--muted)", fontSize: "0.85rem" }}>
            <li>{vaultReady ? "✔" : "✖"} Bóveda {vault == null ? "sin información" : !vault.initialized ? "no inicializada" : vault.unlocked ? "desbloqueada" : "bloqueada (se desbloquea al continuar)"}</li>
            <li>{agentReady ? "✔" : "✖"} Agente local {agentReady ? "disponible" : "no disponible"}</li>
          </ul>
          <Button type="submit" disabled={busy || !vaultReady || !agentReady || ruc.length !== 11 || !solUser || !solPassword || (vaultLocked && !vaultPassword)}>
            {busy ? "Iniciando alta…" : "Iniciar alta asistida"}
          </Button>
        </SecureForm>
      )}

      {step === "waiting" && session && state && (
        <section className="surface-card" style={{ padding: "1.25rem" }} role="status" aria-live="polite">
          <h2 style={{ fontSize: "1.05rem" }}>Obteniendo la Ficha RUC</h2>
          <p style={{ marginTop: "0.4rem" }}>
            <strong>Estado:</strong> {state.label}
          </p>
          <p style={{ marginTop: "0.3rem" }}>{state.description}</p>
          {session.status === "WAITING_FOR_USER" && (
            <div className="simulated-banner" style={{ marginTop: "0.6rem" }}>
              <p>Completa la verificación únicamente en la ventana del navegador local; no ingreses datos en esta página.</p>
              {diagnostic && <p data-testid="onb-waiting-reason" style={{ marginTop: "0.3rem" }}>{diagnostic.message}</p>}
            </div>
          )}
          <p style={{ color: "var(--muted)", marginTop: "0.5rem", fontSize: "0.85rem" }}>
            Cuando la Ficha se obtenga, el navegador local quedará bajo tu control (ciérralo cuando quieras) y aquí aparecerá la propuesta.
          </p>
          <Button type="button" variant="danger" disabled={busy} onClick={handleCancelSession} style={{ marginTop: "0.6rem" }}>
            Cancelar sesión
          </Button>
        </section>
      )}

      {step === "failed" && onboarding && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem" }}>No se obtuvo la Ficha RUC</h2>
          {session && reason && <p style={{ marginTop: "0.4rem" }}>{reason.message}</p>}
          {diagnostic && (
            <p data-testid="onb-diagnostic" style={{ marginTop: "0.3rem" }}>
              {diagnostic.message} <span style={{ color: "var(--muted)" }}>(diagnóstico: {diagnostic.code})</span>
            </p>
          )}
          {!session && <p style={{ marginTop: "0.4rem" }}>La sesión de acceso no llegó a iniciarse.</p>}
          <p style={{ color: "var(--muted)", marginTop: "0.4rem" }}>
            El cliente provisional y la credencial siguen guardados. Puedes reintentar sin volver a escribir las claves o cancelar el alta.
          </p>
          <SecureForm
            onSubmit={() => void handleRetry()}
            style={{ display: "flex", flexDirection: "column", gap: "0.6rem", marginTop: "0.8rem", maxWidth: "420px" }}
          >
            {vaultLocked && (
              <DeliberateInput
                secret
                id="onb-retry-vault-password"
                label="Contraseña de la bóveda (está bloqueada)"
                required
                value={retryVaultPassword}
                onChange={(e) => setRetryVaultPassword(e.target.value)}
              />
            )}
            <div style={{ display: "flex", gap: "0.6rem" }}>
              {canRetryWithoutSecrets(step) && (
                <Button type="submit" disabled={busy || !agentReady || (vaultLocked && !retryVaultPassword)}>
                  Reintentar
                </Button>
              )}
              <Button type="button" variant="danger" disabled={busy} onClick={handleCancelOnboarding}>
                Cancelar alta
              </Button>
            </div>
          </SecureForm>
        </section>
      )}

      {step === "review" && snapshot && onboarding && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.6rem" }}>Revisa la propuesta de SUNAT antes de crear el cliente</h2>
          <FichaReviewPanel
            snapshot={snapshot}
            allowOverrides
            busy={busy}
            confirmLabel="Confirmar alta con los campos marcados"
            onConfirm={(selection) => void handleConfirm(selection)}
            rejectLabel="Rechazar propuesta"
            onReject={() => void handleRejectProposal()}
            extra={
              <label style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} disabled={busy} />
                Activar al cliente (si no, queda como prospecto)
              </label>
            }
          />
          <Button type="button" variant="danger" disabled={busy} onClick={handleCancelOnboarding} style={{ marginTop: "0.8rem" }}>
            Cancelar alta
          </Button>
        </section>
      )}

      {step === "done" && onboarding && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem" }}>Alta finalizada</h2>
          <p style={{ marginTop: "0.4rem" }}>
            El cliente se creó con los datos oficiales que confirmaste. Falta asignar sus obligaciones tributarias (no se infieren
            automáticamente).
          </p>
          <p style={{ marginTop: "0.6rem" }}>
            <Link href={`/clients/${onboarding.client_id}`}>Abrir el contribuyente</Link> ·{" "}
            <Link href={`/clients/${onboarding.client_id}/obligations`}>Asignar obligaciones</Link>
          </p>
        </section>
      )}

      {step === "cancelled" && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem" }}>Alta cancelada</h2>
          <p style={{ marginTop: "0.4rem" }}>La credencial guardada se deshabilitó y el cliente provisional quedó inactivo. Puedes reintentar con el mismo RUC.</p>
          <Button type="button" onClick={() => { setOnboarding(null); setSession(null); setSnapshot(null); router.replace("/clients/new"); }} style={{ marginTop: "0.6rem" }}>
            Empezar de nuevo
          </Button>
        </section>
      )}
    </div>
  );
}
