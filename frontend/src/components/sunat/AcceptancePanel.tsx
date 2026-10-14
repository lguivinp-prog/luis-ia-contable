"use client";

// Aceptacion REAL controlada (M3): solo el propietario/administrador. Autoriza UNA prueba real de UN destino con reconocimiento completo, para ESTE cliente y
// ESTE perfil de credencial; la autorizacion vence pronto y se consume al abrir. No habilita el destino para nadie mas ni cambia estados de cumplimiento.
// PASS exige que el agente haya llegado de verdad a la pantalla oficial; el propietario lo confirma tras verla. Generacion de Registros se abre a mano.
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { createSolSession, fetchAcceptances, grantAcceptance, recordAcceptanceResult, revokeAcceptance } from "@/lib/solAccess";
import { acceptanceStatusLabel, canConfirmAcceptance, currentFailures, describeDiagnostic, proofHeadline, usableProfiles } from "@/lib/solAccessModel";
import type { AcceptanceOut, SolAccessSessionOut, SolDestinationInfo } from "@/lib/types/solAccess";
import type { SolCredentialStatusOut } from "@/lib/types/solVault";

interface Props {
  clientId: string;
  profiles: SolCredentialStatusOut[];
  catalog: SolDestinationInfo[];
  session: SolAccessSessionOut | null;
  starting: boolean;
  /** El agente real y el broker deben estar disponibles; si no, no se ofrece abrir. */
  canOpen: boolean;
  onSession: (s: SolAccessSessionOut) => void;
}

function codeOf(err: unknown): string {
  const detail = (err as { detail?: { code?: string } } | undefined)?.detail;
  return detail?.code ?? "ERROR";
}

export function AcceptancePanel({ clientId, profiles, catalog, session, starting, canOpen, onSession }: Props) {
  const [rows, setRows] = useState<AcceptanceOut[]>([]);
  const [profileId, setProfileId] = useState<string>("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const usable = usableProfiles(profiles);
  const eligible = catalog.filter((d) => d.acceptance_eligible);
  const manualOnly = catalog.filter((d) => d.open_only && d.offered !== false);
  const sessionStatus = session?.status;

  const refresh = useCallback(async () => {
    try {
      setRows((await fetchAcceptances(clientId)).acceptances);
    } catch {
      setRows([]);
    }
  }, [clientId]);

  useEffect(() => {
    void Promise.resolve().then(() => refresh());
  }, [refresh, sessionStatus]);

  const chosen = usable.find((p) => p.id === profileId) ?? (usable.length === 1 ? usable[0] : null);

  async function run(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(`No se pudo completar la acción (${codeOf(err)}).`);
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  const label = (id: string) => catalog.find((d) => d.id === id)?.label ?? id;
  const profileName = (id: string) => profiles.find((p) => p.id === id)?.profile_name ?? "Credencial";
  const live = (r: AcceptanceOut) => r.status === "AUTHORIZED"; // el servidor materializa el vencimiento al listar y lo vuelve a exigir al abrir
  const pendingResult = rows.filter(canConfirmAcceptance);
  // La vista principal muestra el resultado VIGENTE por destino y perfil: un exito posterior reemplaza la alerta de un fallo anterior (que sigue en el historial).
  const failed = currentFailures(rows);
  const validated = rows.filter((r) => proofHeadline(rows, r.destination, r.credential_id)?.row.id === r.id && r.phase === "CONFIRMED_PASS");
  const running = rows.filter((r) => r.phase === "CONSUMED_RUNNING");

  if (eligible.length === 0 && manualOnly.length === 0) return null;

  return (
    <section data-testid="acceptance-panel" aria-labelledby="acceptance-title" style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <div>
        <h2 id="acceptance-title" style={{ fontSize: "1.05rem", marginBottom: "0.3rem" }}>Prueba real controlada de destinos</h2>
        <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          Solo propietario. Autoriza <strong>una</strong> apertura real de un destino aún no habilitado, para este cliente y el perfil elegido. Vence en 30
          minutos, se usa una sola vez y no habilita el destino para nadie más. El agente solo abre la pantalla y te deja el control: no declara, no paga
          ni modifica nada.
        </p>
      </div>
      {error && <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>{error}</div>}

      {usable.length === 0 ? (
        <p>Registra primero una credencial SOL utilizable en la bóveda (formulario seguro).</p>
      ) : (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
          Perfil de credencial para la prueba
          <select value={chosen?.id ?? ""} onChange={(e) => setProfileId(e.target.value)} disabled={busy} data-testid="acceptance-profile">
            {usable.length > 1 && <option value="">Elige un perfil…</option>}
            {usable.map((p) => (
              <option key={p.id} value={p.id}>{p.profile_name ?? "Credencial"}</option>
            ))}
          </select>
        </label>
      )}

      <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.6rem" }}>
        {eligible.map((d) => {
          const grant = chosen ? rows.find((r) => live(r) && r.destination === d.id && r.credential_id === chosen.id) : undefined;
          const head = chosen ? proofHeadline(rows, d.id, chosen.id) : null;
          const proven = chosen ? rows.some((r) => r.phase === "CONFIRMED_PASS" && r.destination === d.id && r.credential_id === chosen.id) : false;
          return (
            <li key={d.id} data-testid={`acceptance-${d.id}`} data-headline={head?.key ?? "NONE"} style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.6rem" }}>
              <strong>{d.label}</strong>{" "}
              <span style={{ color: proven ? "var(--success, #2e7d32)" : "var(--muted)" }}>
                · {proven ? "probado con este perfil" : d.enabled ? "estructura validada · este perfil aún sin prueba" : "sin habilitar globalmente"}
              </span>
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.4rem", alignItems: "center" }}>
                {proven ? (
                  <span data-testid={`acceptance-proven-${d.id}`} style={{ fontSize: "0.85rem" }}>
                    Disponible para este perfil: ábrelo desde «Abrir en SUNAT». No necesita otra autorización.
                  </span>
                ) : grant ? (
                  <>
                    <Button
                      type="button"
                      disabled={busy || starting || !canOpen || (session != null && !session.is_terminal)}
                      onClick={() => void run(async () => onSession(await createSolSession(clientId, crypto.randomUUID(), { destination: d.id, credentialId: grant.credential_id })))}
                    >
                      Abrir ahora (prueba real)
                    </Button>
                    <Button type="button" variant="secondary" disabled={busy} onClick={() => void run(() => revokeAcceptance(grant.id))}>
                      Revocar autorización
                    </Button>
                    <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Vence {new Date(grant.expires_at).toLocaleTimeString()}</span>
                  </>
                ) : (
                  <>
                    <label style={{ display: "flex", gap: "0.4rem", alignItems: "center", fontSize: "0.85rem" }}>
                      <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} disabled={busy} />
                      Autorizo un acceso real a SUNAT con este perfil
                    </label>
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={busy || !chosen || !confirm}
                      onClick={() => chosen && void run(async () => { await grantAcceptance(clientId, chosen.id, d.id); setConfirm(false); })}
                    >
                      Autorizar prueba
                    </Button>
                  </>
                )}
              </div>
            </li>
          );
        })}
        {manualOnly.map((d) => (
          <li key={d.id} data-testid={`acceptance-manual-${d.id}`} style={{ fontSize: "0.85rem" }}>
            <strong>{d.label}</strong> · acceso <strong>manual</strong> desde Menú SOL (con tu intervención). No se automatiza ni se prueba aquí.
          </li>
        ))}
      </ul>

      {running.length > 0 && (
        <p data-testid="acceptance-running" role="status" style={{ fontSize: "0.85rem" }}>
          Hay una prueba en curso ({running.map((r) => label(r.destination)).join(", ")}). Cuando el agente te entregue el navegador podrás confirmar el resultado.
        </p>
      )}
      {validated.length > 0 && (
        <div data-testid="acceptance-validated" role="status" style={{ border: "1px solid var(--success, #2e7d32)", borderRadius: "8px", padding: "0.6rem" }}>
          <strong>Pruebas reales superadas</strong>
          <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem", fontSize: "0.85rem" }}>
            {validated.map((r) => (
              <li key={r.id}>{label(r.destination)} · {profileName(r.credential_id)}: llegada validada y confirmada por el propietario.</li>
            ))}
          </ul>
        </div>
      )}
      {failed.length > 0 && (
        <div data-testid="acceptance-failed" role="alert" style={{ border: "1px solid var(--danger, #c0392b)", borderRadius: "8px", padding: "0.6rem" }}>
          <strong>La última prueba falló antes de llegar al destino</strong>
          <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem", fontSize: "0.85rem" }}>
            {failed.slice(0, 3).map((r) => {
              const d = describeDiagnostic(r.diagnostic_code);
              return (
                <li key={r.id}>
                  {label(r.destination)} · {profileName(r.credential_id)}: {d ? d.message : "fallo técnico sin diagnóstico."}
                  {d ? <span style={{ color: "var(--muted)" }}> (diagnóstico: {d.code})</span> : null}
                </li>
              );
            })}
          </ul>
          <p style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: "0.3rem" }}>
            No cuenta como aceptación ni puede confirmarse como correcta. La autorización ya se usó: para reintentar, autoriza una nueva prueba cuando quieras.
          </p>
        </div>
      )}
      {pendingResult.length > 0 && (
        <div data-testid="acceptance-pending-result" style={{ border: "1px solid var(--border, #ddd)", borderRadius: "8px", padding: "0.6rem" }}>
          <strong>Confirma el resultado de la prueba</strong>
          <p style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
            Marca «Correcta» solo si viste la pantalla oficial del destino indicado. «No coincide» deja el destino deshabilitado.
          </p>
          <input aria-label="Nota breve (sin datos del contribuyente)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} style={{ width: "100%" }} />
          {pendingResult.map((r) => (
            <div key={r.id} style={{ display: "flex", gap: "0.5rem", marginTop: "0.4rem", flexWrap: "wrap", alignItems: "center" }}>
              <span>{label(r.destination)} · {profileName(r.credential_id)}</span>
              <Button type="button" disabled={busy} onClick={() => void run(() => recordAcceptanceResult(r.id, "PASS", note))}>Correcta</Button>
              <Button type="button" variant="secondary" disabled={busy} onClick={() => void run(() => recordAcceptanceResult(r.id, "FAIL", note))}>No coincide</Button>
            </div>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer" }}>Historial de pruebas</summary>
          <ul style={{ listStyle: "none", padding: 0, fontSize: "0.85rem" }}>
            {rows.map((r) => (
              <li key={r.id}>{label(r.destination)} · {profileName(r.credential_id)} · {acceptanceStatusLabel(r)}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
