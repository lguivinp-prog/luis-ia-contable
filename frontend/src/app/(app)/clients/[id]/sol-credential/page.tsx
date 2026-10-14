"use client";

import { SecureForm } from "@/components/ui/SecureForm";
import { DeliberateInput } from "@/components/ui/DeliberateInput";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { SOL_CREDENTIAL_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { useCurrentUser } from "@/lib/currentUserContext";
import {
  createClientSolCredentialProfile,
  disableClientSolCredentialProfile,
  fetchClientSolCredentials,
  updateClientSolCredentialProfile,
} from "@/lib/solVault";
import { CREDENTIAL_FIELD_LIMITS, describeSolVaultError, findPersistedProfile, validateCredentialForm } from "@/lib/solVaultErrors";
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

// Un cliente puede tener varios perfiles (una principal y una o varias secundarias con autorizaciones distintas). Cada perfil se cifra por
// separado en la bóveda; el nombre del perfil es solo una etiqueta INTERNA: los permisos reales los comprueba el portal, no el nombre.
export default function ClientSolCredentialPage() {
  const params = useParams<{ id: string }>();
  const clientId = params.id;
  const { user } = useCurrentUser();
  const canRead = canReadSolCredential(user);
  const canCreate = canCreateSolCredential(user);
  const canUpdate = canUpdateSolCredential(user);
  const canDisable = canDisableSolCredential(user);

  const [profiles, setProfiles] = useState<SolCredentialStatusOut[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null); // null => registrar un perfil NUEVO
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProfiles(await fetchClientSolCredentials(clientId));
    } catch {
      setError("No se pudo cargar el estado de la credencial SOL.");
    } finally {
      setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  const editing = profiles.find((p) => p.id === editingId) ?? null;

  const [credentialKind, setCredentialKind] = useState<SolCredentialKind>("SECONDARY");
  const [profileName, setProfileName] = useState("");
  const [solUsername, setSolUsername] = useState("");
  const [solPassword, setSolPassword] = useState("");
  const [authorizationBasis, setAuthorizationBasis] = useState("");
  const [authorizationExpiresAt, setAuthorizationExpiresAt] = useState("");
  const [authorizationReference, setAuthorizationReference] = useState("");
  const [authorizedScopeDescription, setAuthorizedScopeDescription] = useState("");

  function resetForm() {
    setProfileName("");
    setSolUsername("");
    setSolPassword("");
    setAuthorizationBasis("");
    setAuthorizationExpiresAt("");
    setAuthorizationReference("");
    setAuthorizedScopeDescription("");
  }

  // Un solo envio a la vez (doble clic / Enter repetido): `working` se actualiza de forma asincrona, la referencia no.
  const submitting = useRef(false);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    setError(null);
    setBanner(null);
    const invalid = validateCredentialForm({ profileName, authorizationBasis, authorizationReference, authorizedScopeDescription });
    if (invalid) {
      setError(invalid);
      return;
    }
    submitting.current = true;
    setWorking(true);
    const before = profiles;
    try {
      await createClientSolCredentialProfile(clientId, {
        credential_kind: credentialKind,
        profile_name: profileName.trim() || null,
        sol_username: solUsername,
        sol_password: solPassword,
        authorization_basis: authorizationBasis,
        authorization_expires_at: authorizationExpiresAt ? new Date(authorizationExpiresAt).toISOString() : null,
        authorization_reference: authorizationReference || null,
        authorized_scope_description: authorizedScopeDescription || null,
      });
      resetForm();
      setBanner("Credencial guardada. Quedó pendiente de validación.");
      await reload();
    } catch (err) {
      // Un fallo no oculta una operacion ya confirmada: se relee la lista y, si el perfil aparece, NO se pide reenviar.
      let fresh = before;
      try {
        fresh = await fetchClientSolCredentials(clientId);
        setProfiles(fresh);
      } catch {
        /* se conserva el mensaje del fallo original */
      }
      const saved = findPersistedProfile(before, fresh, credentialKind, profileName);
      if (saved) {
        resetForm();
        setBanner(`La credencial «${saved.profile_name ?? "perfil"}» ya estaba guardada (quedó pendiente de validación). No hace falta repetir el registro.`);
      } else {
        setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
      }
    } finally {
      setWorking(false);
      submitting.current = false;
    }
  }

  async function handleUpdate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    setError(null);
    setWorking(true);
    try {
      await updateClientSolCredentialProfile(clientId, editing.id, {
        expected_version: editing.version,
        profile_name: profileName.trim() || undefined,
        sol_username: solUsername || undefined,
        sol_password: solPassword || undefined,
        authorization_basis: authorizationBasis || undefined,
        authorization_expires_at: authorizationExpiresAt ? new Date(authorizationExpiresAt).toISOString() : undefined,
        authorization_reference: authorizationReference || undefined,
        authorized_scope_description: authorizedScopeDescription || undefined,
      });
      resetForm();
      setEditingId(null);
      setBanner("Credencial actualizada. Quedó pendiente de validación.");
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleDisable(profile: SolCredentialStatusOut) {
    const confirmed = window.confirm(`¿Confirmas dejar de usar el perfil «${profile.profile_name ?? "credencial"}»? Los demás perfiles no se tocan.`);
    if (!confirmed) return;
    setError(null);
    setWorking(true);
    try {
      await disableClientSolCredentialProfile(clientId, profile.id, profile.version);
      if (editingId === profile.id) setEditingId(null);
      setBanner("Perfil deshabilitado. Los demás perfiles siguen intactos.");
      await reload();
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
        <p style={{ color: "var(--muted)", marginTop: "0.35rem", fontSize: "0.9rem" }}>
          Puedes registrar una credencial principal y una o varias secundarias con autorizaciones distintas. El nombre del perfil es solo una etiqueta
          interna: lo que cada credencial puede abrir lo comprueba el portal en cada acceso, nunca se supone. Escribe las claves únicamente en este formulario seguro.
        </p>
      </div>

      {banner && <div className="simulated-banner">{banner}</div>}
      {error && (
        <div className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>
          {error}
        </div>
      )}

      {profiles.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>Este cliente no tiene una credencial SOL registrada.</p>
      ) : (
        profiles.map((credential) => (
          <section key={credential.id} className="surface-card" style={{ padding: "1.25rem" }} data-testid={`profile-${credential.id}`}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem", flexWrap: "wrap" }}>
              <strong>{credential.profile_name ?? "Credencial"}</strong>
              <StatusSemaphore status={credential.status} map={SOL_CREDENTIAL_STATUS_SEMAPHORE} />
              <span style={{ color: "var(--muted)" }}>{CREDENTIAL_KIND_LABELS[credential.credential_kind] ?? credential.credential_kind}</span>
            </div>
            <p style={{ marginBottom: "0.35rem" }}>
              <strong>Motivo de autorización:</strong> {credential.authorization_basis}
            </p>
            {credential.authorization_expires_at && (
              <p style={{ marginBottom: "0.35rem" }}>
                <strong>Autorización vence:</strong> {new Date(credential.authorization_expires_at).toLocaleString("es-PE")}
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
            <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
              {canUpdate && (
                <Button type="button" variant="secondary" onClick={() => { setEditingId(credential.id); resetForm(); setProfileName(credential.profile_name ?? ""); }} disabled={working}>
                  Editar este perfil
                </Button>
              )}
              {canDisable && (
                <Button type="button" variant="danger" onClick={() => void handleDisable(credential)} disabled={working}>
                  Dejar de usar este perfil
                </Button>
              )}
            </div>
          </section>
        ))
      )}

      {((!editing && canCreate) || (editing && canUpdate)) && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>
            {editing ? `Actualizar «${editing.profile_name ?? "credencial"}»` : profiles.length > 0 ? "Agregar otro perfil de credencial" : "Registrar credencial SOL"}
          </h2>
          <SecureForm
            onSubmit={editing ? handleUpdate : handleCreate}
            style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}
          >
            {!editing && (
              <Select
                label="Tipo de credencial"
                options={CREDENTIAL_KIND_OPTIONS}
                value={credentialKind}
                onChange={(e) => setCredentialKind(e.target.value as SolCredentialKind)}
              />
            )}
            <Input
              label="Nombre interno del perfil (no es secreto)"
              maxLength={CREDENTIAL_FIELD_LIMITS.profile_name}
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder={editing ? undefined : "Ej.: Contador delegado"}
            />
            <DeliberateInput
              label="Usuario SOL"
              required={!editing}
              value={solUsername}
              onChange={(e) => setSolUsername(e.target.value)}
              hint={
                editing ? (
                  <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
                    Déjalo en blanco para no cambiarlo.
                  </span>
                ) : undefined
              }
            />
            <DeliberateInput secret
              label="Contraseña SOL"
              required={!editing}
              value={solPassword}
              onChange={(e) => setSolPassword(e.target.value)}
              hint={
                editing ? (
                  <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
                    Déjala en blanco para no cambiarla.
                  </span>
                ) : undefined
              }
            />
            <Input
              label="Motivo de autorización (breve, hasta 60 caracteres)"
              required={!editing}
              maxLength={CREDENTIAL_FIELD_LIMITS.authorization_basis}
              value={authorizationBasis}
              onChange={(e) => setAuthorizationBasis(e.target.value)}
              hint={
                <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>
                  {authorizationBasis.length}/{CREDENTIAL_FIELD_LIMITS.authorization_basis} · El detalle va en «Descripción del alcance autorizado».
                </span>
              }
            />
            <Input
              label="La autorización vence (opcional)"
              type="datetime-local"
              value={authorizationExpiresAt}
              onChange={(e) => setAuthorizationExpiresAt(e.target.value)}
            />
            <Input
              label="Referencia de la autorización (opcional)"
              maxLength={CREDENTIAL_FIELD_LIMITS.authorization_reference}
              value={authorizationReference}
              onChange={(e) => setAuthorizationReference(e.target.value)}
            />
            <Input
              label="Descripción del alcance autorizado (opcional)"
              maxLength={CREDENTIAL_FIELD_LIMITS.authorized_scope_description}
              value={authorizedScopeDescription}
              onChange={(e) => setAuthorizedScopeDescription(e.target.value)}
            />
            <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
              <Button type="submit" disabled={working}>
                {editing ? "Guardar cambios" : "Registrar credencial"}
              </Button>
              {editing && (
                <Button type="button" variant="secondary" onClick={() => { setEditingId(null); resetForm(); }} disabled={working}>
                  Cancelar edición
                </Button>
              )}
            </div>
          </SecureForm>
        </section>
      )}
    </div>
  );
}
