"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { SOL_CREDENTIAL_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { useCurrentUser } from "@/lib/currentUserContext";
import {
  createClientSolCredential,
  disableClientSolCredential,
  fetchClientSolCredentialStatus,
  updateClientSolCredential,
} from "@/lib/solVault";
import { describeSolVaultError } from "@/lib/solVaultErrors";
import {
  canCreateSolCredential,
  canDisableSolCredential,
  canReadSolCredential,
  canUpdateSolCredential,
} from "@/lib/solVaultPermissions";
import type { SolCredentialKind, SolCredentialStatusOut } from "@/lib/types/solVault";

const CREDENTIAL_KIND_OPTIONS = [
  { value: "SECONDARY", label: "Secundaria (clave SOL propia del despacho)" },
  { value: "PRIMARY_OWN_RUC", label: "Principal (RUC propio del contribuyente)" },
  { value: "PRIMARY_EXCEPTIONAL", label: "Principal (excepción autorizada)" },
];

const CREDENTIAL_KIND_LABELS: Record<string, string> = {
  SECONDARY: "Secundaria",
  PRIMARY_OWN_RUC: "Principal (RUC propio)",
  PRIMARY_EXCEPTIONAL: "Principal (excepción autorizada)",
};

export default function ClientSolCredentialPage() {
  const params = useParams<{ id: string }>();
  const clientId = params.id;
  const { user } = useCurrentUser();
  const canRead = canReadSolCredential(user);
  const canCreate = canCreateSolCredential(user);
  const canUpdate = canUpdateSolCredential(user);
  const canDisable = canDisableSolCredential(user);

  const [credential, setCredential] = useState<SolCredentialStatusOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchClientSolCredentialStatus(clientId);
      setCredential(data);
    } catch {
      setError("No se pudo cargar el estado de la credencial SOL.");
    } finally {
      setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  // --- alta / actualizacion (mismo formulario: crea si no existe, actualiza si existe) ---
  const [credentialKind, setCredentialKind] = useState<SolCredentialKind>("SECONDARY");
  const [solUsername, setSolUsername] = useState("");
  const [solPassword, setSolPassword] = useState("");
  const [authorizationBasis, setAuthorizationBasis] = useState("");
  const [authorizationExpiresAt, setAuthorizationExpiresAt] = useState("");
  const [authorizationReference, setAuthorizationReference] = useState("");
  const [authorizedScopeDescription, setAuthorizedScopeDescription] = useState("");

  function resetForm() {
    setSolUsername("");
    setSolPassword("");
    setAuthorizationBasis("");
    setAuthorizationExpiresAt("");
    setAuthorizationReference("");
    setAuthorizedScopeDescription("");
  }

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWorking(true);
    try {
      const created = await createClientSolCredential(clientId, {
        credential_kind: credentialKind,
        sol_username: solUsername,
        sol_password: solPassword,
        authorization_basis: authorizationBasis,
        authorization_expires_at: authorizationExpiresAt ? new Date(authorizationExpiresAt).toISOString() : null,
        authorization_reference: authorizationReference || null,
        authorized_scope_description: authorizedScopeDescription || null,
      });
      setCredential(created);
      resetForm();
      setBanner("Credencial guardada. Quedó pendiente de validación.");
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleUpdate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!credential) return;
    setError(null);
    setWorking(true);
    try {
      const updated = await updateClientSolCredential(clientId, {
        expected_version: credential.version,
        sol_username: solUsername || undefined,
        sol_password: solPassword || undefined,
        authorization_basis: authorizationBasis || undefined,
        authorization_expires_at: authorizationExpiresAt
          ? new Date(authorizationExpiresAt).toISOString()
          : undefined,
        authorization_reference: authorizationReference || undefined,
        authorized_scope_description: authorizedScopeDescription || undefined,
      });
      setCredential(updated);
      resetForm();
      setBanner("Credencial actualizada. Quedó pendiente de validación.");
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleDisable() {
    if (!credential) return;
    const confirmed = window.confirm("¿Confirmas deshabilitar esta credencial SOL?");
    if (!confirmed) return;
    setError(null);
    setWorking(true);
    try {
      const disabled = await disableClientSolCredential(clientId, credential.version);
      setCredential(disabled);
      setBanner("Credencial deshabilitada.");
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  if (loading) {
    return <p>Cargando…</p>;
  }

  if (!canRead) {
    return <p>No tienes permiso para ver la credencial SOL de este cliente.</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "640px" }}>
      <div>
        <h1 style={{ fontSize: "1.4rem" }}>Credencial SOL del cliente</h1>
        <p style={{ color: "var(--muted)", marginTop: "0.35rem" }}>
          Luis IA Contable nunca muestra nuevamente una contraseña SOL guardada.
        </p>
      </div>

      {banner && <div className="simulated-banner">{banner}</div>}
      {error && (
        <div className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>
          {error}
        </div>
      )}

      {credential ? (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem" }}>
            <strong>Estado:</strong>
            <StatusSemaphore status={credential.status} map={SOL_CREDENTIAL_STATUS_SEMAPHORE} />
            <span style={{ color: "var(--muted)" }}>
              {CREDENTIAL_KIND_LABELS[credential.credential_kind] ?? credential.credential_kind}
            </span>
          </div>
          <p style={{ marginBottom: "0.35rem" }}>
            <strong>Motivo de autorización:</strong> {credential.authorization_basis}
          </p>
          {credential.authorization_expires_at && (
            <p style={{ marginBottom: "0.35rem" }}>
              <strong>Autorización vence:</strong>{" "}
              {new Date(credential.authorization_expires_at).toLocaleString("es-PE")}
            </p>
          )}
          {credential.authorization_reference && (
            <p style={{ marginBottom: "0.35rem" }}>
              <strong>Referencia:</strong> {credential.authorization_reference}
            </p>
          )}
          {credential.status === "NEEDS_VALIDATION" && (
            <p style={{ marginTop: "0.75rem", fontWeight: 600 }}>
              La credencial quedará pendiente de validación hasta comprobarla mediante un acceso supervisado
              posterior.
            </p>
          )}
          {canDisable && credential.status !== "DISABLED" && (
            <Button type="button" variant="danger" onClick={handleDisable} disabled={working} style={{ marginTop: "0.75rem" }}>
              Deshabilitar credencial
            </Button>
          )}
        </section>
      ) : (
        <p style={{ color: "var(--muted)" }}>Este cliente no tiene una credencial SOL registrada.</p>
      )}

      {((!credential && canCreate) || (credential && credential.status !== "DISABLED" && canUpdate)) && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>
            {credential ? "Actualizar credencial" : "Registrar credencial SOL"}
          </h2>
          <form
            onSubmit={credential ? handleUpdate : handleCreate}
            style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}
          >
            {!credential && (
              <Select
                label="Tipo de credencial"
                options={CREDENTIAL_KIND_OPTIONS}
                value={credentialKind}
                onChange={(e) => setCredentialKind(e.target.value as SolCredentialKind)}
              />
            )}
            <Input
              label="Usuario SOL"
              required={!credential}
              value={solUsername}
              onChange={(e) => setSolUsername(e.target.value)}
              hint={
                credential ? (
                  <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
                    Déjalo en blanco para no cambiarlo.
                  </span>
                ) : undefined
              }
            />
            <Input
              label="Contraseña SOL"
              type="password"
              required={!credential}
              value={solPassword}
              onChange={(e) => setSolPassword(e.target.value)}
              hint={
                credential ? (
                  <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
                    Déjala en blanco para no cambiarla.
                  </span>
                ) : undefined
              }
            />
            <Input
              label="Motivo de autorización"
              required={!credential}
              value={authorizationBasis}
              onChange={(e) => setAuthorizationBasis(e.target.value)}
            />
            <Input
              label="La autorización vence (opcional)"
              type="datetime-local"
              value={authorizationExpiresAt}
              onChange={(e) => setAuthorizationExpiresAt(e.target.value)}
            />
            <Input
              label="Referencia de la autorización (opcional)"
              value={authorizationReference}
              onChange={(e) => setAuthorizationReference(e.target.value)}
            />
            <Input
              label="Descripción del alcance autorizado (opcional)"
              value={authorizedScopeDescription}
              onChange={(e) => setAuthorizedScopeDescription(e.target.value)}
            />
            <Button type="submit" disabled={working}>
              {credential ? "Guardar cambios" : "Registrar credencial"}
            </Button>
          </form>
        </section>
      )}
    </div>
  );
}
