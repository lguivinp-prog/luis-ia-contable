"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import {
  SOL_VAULT_INVITATION_STATUS_SEMAPHORE,
  SOL_VAULT_SLOT_STATUS_SEMAPHORE,
  SOL_VAULT_STATUS_SEMAPHORE,
  StatusSemaphore,
} from "@/components/ui/StatusSemaphore";
import { useCurrentUser } from "@/lib/currentUserContext";
import {
  canInitializeSolVault,
  canManageSolVaultUsers,
  canRecoverSolVault,
  canRotateSolVault,
  canViewSolVaultNav,
} from "@/lib/solVaultPermissions";
import { describeSolVaultError } from "@/lib/solVaultErrors";
import {
  acceptSolVaultInvitation,
  cancelSolVaultInvitation,
  changeSolVaultPassword,
  confirmSolVaultRecovery,
  createSolVaultInvitation,
  fetchSolVaultStatus,
  initializeSolVault,
  listSolVaultSlots,
  lockSolVault,
  reauthorizeSolVaultInvitation,
  recoverSolVault,
  replaceSolVaultRecovery,
  revokeSolVaultSlot,
  rotateSolVaultDataKey,
  unlockSolVault,
} from "@/lib/solVault";
import { isValidVaultPassword, VAULT_PASSWORD_MAX_LENGTH, VAULT_PASSWORD_MIN_LENGTH } from "@/lib/solVaultValidation";
import type { InvitationOut, SlotOut, SolVaultStatusOut } from "@/lib/types/solVault";
import { listUsers } from "@/lib/users";
import type { ManagedUser } from "@/lib/types/user";

export default function SolVaultPage() {
  const { user } = useCurrentUser();
  const canUnlock = canViewSolVaultNav(user);
  const canInitialize = canInitializeSolVault(user);
  const canRecover = canRecoverSolVault(user);
  const canRotate = canRotateSolVault(user);
  const canManageUsers = canManageSolVaultUsers(user);

  const [status, setStatus] = useState<SolVaultStatusOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSolVaultStatus();
      setStatus(data);
    } catch {
      setError("No se pudo cargar el estado de la bóveda SOL.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  // --- slots (declarado aqui, antes de handleRecover, para que
  // React Compiler pueda resolver la referencia hacia adelante) ---
  const [slots, setSlots] = useState<SlotOut[]>([]);
  const [slotsLoaded, setSlotsLoaded] = useState(false);

  const reloadSlots = useCallback(async () => {
    try {
      const data = await listSolVaultSlots();
      setSlots(data.items);
      setSlotsLoaded(true);
    } catch {
      // se muestra en la seccion de errores general si el usuario reintenta una accion
    }
  }, []);

  // --- inicializacion ---
  const [initPassword, setInitPassword] = useState("");
  const [pendingRecoveryCode, setPendingRecoveryCode] = useState<string | null>(null);
  const [confirmCodeInput, setConfirmCodeInput] = useState("");

  async function handleInitialize(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWorking(true);
    try {
      const result = await initializeSolVault(initPassword);
      setPendingRecoveryCode(result.recovery_code);
      setBanner(result.message);
      setInitPassword("");
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleConfirmRecovery() {
    setError(null);
    setWorking(true);
    try {
      await confirmSolVaultRecovery(confirmCodeInput);
      setPendingRecoveryCode(null);
      setConfirmCodeInput("");
      setBanner("Código de recuperación confirmado.");
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- recuperacion (RECOVERY_REQUIRED) ---
  const [recoverCode, setRecoverCode] = useState("");
  const [recoverNewPassword, setRecoverNewPassword] = useState("");

  async function handleRecover(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWorking(true);
    try {
      await recoverSolVault(recoverCode, recoverNewPassword);
      setRecoverCode("");
      setRecoverNewPassword("");
      setBanner("Acceso recuperado correctamente.");
      await reloadSlots();
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- desbloqueo / bloqueo ---
  const [unlockPassword, setUnlockPassword] = useState("");

  async function handleUnlock(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWorking(true);
    try {
      await unlockSolVault(unlockPassword);
      setUnlockPassword("");
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleLock() {
    setError(null);
    setWorking(true);
    try {
      await lockSolVault();
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- cambio de contrasena personal ---
  const [newPassword, setNewPassword] = useState("");

  async function handleChangePassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWorking(true);
    try {
      await changeSolVaultPassword(newPassword);
      setNewPassword("");
      setBanner("Contraseña de bóveda actualizada.");
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- reemplazo de codigo de recuperacion ---
  const [pendingReplacementCode, setPendingReplacementCode] = useState<string | null>(null);
  const [confirmReplacementInput, setConfirmReplacementInput] = useState("");

  async function handleReplaceRecovery() {
    if (!status?.version) return;
    setError(null);
    setWorking(true);
    try {
      const result = await replaceSolVaultRecovery(status.version);
      setPendingReplacementCode(result.new_recovery_code);
      setBanner(result.message);
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleConfirmReplacement() {
    setError(null);
    setWorking(true);
    try {
      await confirmSolVaultRecovery(confirmReplacementInput);
      setPendingReplacementCode(null);
      setConfirmReplacementInput("");
      setBanner("Nuevo código de recuperación confirmado.");
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- rotacion de data key ---
  async function handleRotate() {
    if (!status?.version) return;
    const confirmed = window.confirm(
      "¿Confirmas rotar la clave de cifrado de las credenciales? Todas las credenciales SOL se recifran con una clave nueva.",
    );
    if (!confirmed) return;
    setError(null);
    setWorking(true);
    try {
      const result = await rotateSolVaultDataKey(status.version);
      setBanner(`${result.message} (versión ${result.new_data_key_version}).`);
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- slots (estado y reloadSlots declarados mas arriba en el componente,
  // antes de handleRecover, para que el analisis estatico de memoizacion de
  // React Compiler pueda resolver la referencia hacia adelante) ---

  useEffect(() => {
    if (canManageUsers && status?.initialized && !slotsLoaded) {
      listSolVaultSlots()
        .then((data) => {
          setSlots(data.items);
          setSlotsLoaded(true);
        })
        .catch(() => {
          // se muestra en la seccion de errores general si el usuario reintenta una accion
        });
    }
  }, [canManageUsers, status?.initialized, slotsLoaded]);

  async function handleRevokeSlot(slot: SlotOut) {
    if (!status?.version) return;
    const confirmed = window.confirm(
      `¿Confirmas revocar este acceso (${slot.kind === "USER" ? "usuario" : "recuperación"})?`,
    );
    if (!confirmed) return;
    setError(null);
    setWorking(true);
    try {
      await revokeSolVaultSlot(slot.id, status.version);
      await reloadSlots();
      await reload();
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- invitaciones ---
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [usersLoaded, setUsersLoaded] = useState(false);
  const [inviteTargetUserId, setInviteTargetUserId] = useState("");
  const [invitations, setInvitations] = useState<InvitationOut[]>([]);

  useEffect(() => {
    if (canManageUsers && !usersLoaded) {
      listUsers()
        .then((data) => {
          setUsers(data);
          setUsersLoaded(true);
        })
        .catch(() => {
          // el selector de destinatario simplemente queda vacio
        });
    }
  }, [canManageUsers, usersLoaded]);

  async function handleCreateInvitation() {
    if (!inviteTargetUserId) return;
    setError(null);
    setWorking(true);
    try {
      const invitation = await createSolVaultInvitation(inviteTargetUserId);
      setInvitations((prev) => [invitation, ...prev]);
      setInviteTargetUserId("");
      setBanner("Invitación creada. Comparte el identificador con la persona invitada.");
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleReauthorizeInvitation(invitation: InvitationOut) {
    setError(null);
    setWorking(true);
    try {
      await reauthorizeSolVaultInvitation(invitation.id, invitation.version);
      setInvitations((prev) =>
        prev.map((i) => (i.id === invitation.id ? { ...i, version: i.version + 1 } : i)),
      );
      setBanner("Invitación reautorizada.");
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  async function handleCancelInvitation(invitation: InvitationOut) {
    const confirmed = window.confirm("¿Confirmas cancelar esta invitación?");
    if (!confirmed) return;
    setError(null);
    setWorking(true);
    try {
      await cancelSolVaultInvitation(invitation.id, invitation.version);
      setInvitations((prev) =>
        prev.map((i) => (i.id === invitation.id ? { ...i, status: "CANCELLED" } : i)),
      );
    } catch (err) {
      setError(describeSolVaultError((err as { detail?: unknown } | undefined)?.detail));
    } finally {
      setWorking(false);
    }
  }

  // --- aceptar invitacion (cualquier usuario con sol_vault:unlock) ---
  const [acceptInvitationId, setAcceptInvitationId] = useState("");
  const [acceptPassword, setAcceptPassword] = useState("");

  async function handleAcceptInvitation(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWorking(true);
    try {
      await acceptSolVaultInvitation(acceptInvitationId, acceptPassword);
      setAcceptInvitationId("");
      setAcceptPassword("");
      setBanner("Invitación aceptada. Ya tienes tu propio acceso a la bóveda.");
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

  if (!canUnlock) {
    return <p>No tienes permiso para ver la bóveda de credenciales SOL.</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem", maxWidth: "760px" }}>
      <div>
        <h1 style={{ fontSize: "1.4rem" }}>Bóveda de credenciales SOL</h1>
        <p style={{ color: "var(--muted)", marginTop: "0.35rem" }}>
          La bóveda se bloquea al reiniciar la aplicación.
        </p>
      </div>

      {banner && <div className="simulated-banner">{banner}</div>}
      {error && (
        <div className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>
          {error}
        </div>
      )}

      {status && (
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <strong>Estado:</strong>
          <StatusSemaphore status={status.status} map={SOL_VAULT_STATUS_SEMAPHORE} />
          {status.initialized && (
            <span className={`status-pill ${status.unlocked ? "status-verde" : "status-gris"}`}>
              {status.unlocked ? "Desbloqueada" : "Bloqueada"}
            </span>
          )}
        </div>
      )}

      {pendingRecoveryCode && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Confirma tu código de recuperación</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <p>
              <strong>Código de recuperación (única vez que se muestra):</strong>
            </p>
            <code
              style={{
                padding: "0.6rem",
                background: "var(--surface-alt, #f4f4f4)",
                wordBreak: "break-all",
              }}
            >
              {pendingRecoveryCode}
            </code>
            <p style={{ fontWeight: 600 }}>Guarda el código de recuperación fuera de esta computadora.</p>
            <Input
              label="Escribe el código para confirmar que lo guardaste"
              value={confirmCodeInput}
              onChange={(e) => setConfirmCodeInput(e.target.value)}
            />
            <Button type="button" onClick={handleConfirmRecovery} disabled={working || !confirmCodeInput}>
              Confirmar código de recuperación
            </Button>
          </div>
        </section>
      )}

      {!pendingRecoveryCode && !status?.initialized && canInitialize && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Inicializar bóveda</h2>
          <form onSubmit={handleInitialize} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <Input
              label="Contraseña personal de la bóveda"
              type="password"
              minLength={VAULT_PASSWORD_MIN_LENGTH}
              maxLength={VAULT_PASSWORD_MAX_LENGTH}
              required
              value={initPassword}
              onChange={(e) => setInitPassword(e.target.value)}
              hint={<span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>Mínimo 14 caracteres. Puede ser una frase.</span>}
            />
            <Button type="submit" disabled={working || !isValidVaultPassword(initPassword)}>
              Inicializar bóveda
            </Button>
          </form>
        </section>
      )}

      {status?.status === "RECOVERY_REQUIRED" && canRecover && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Recuperar acceso</h2>
          <form onSubmit={handleRecover} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <Input
              label="Código de recuperación"
              required
              value={recoverCode}
              onChange={(e) => setRecoverCode(e.target.value)}
            />
            <Input
              label="Nueva contraseña personal"
              type="password"
              minLength={VAULT_PASSWORD_MIN_LENGTH}
              maxLength={VAULT_PASSWORD_MAX_LENGTH}
              required
              value={recoverNewPassword}
              onChange={(e) => setRecoverNewPassword(e.target.value)}
            />
            <Button type="submit" disabled={working || !isValidVaultPassword(recoverNewPassword)}>
              Recuperar acceso
            </Button>
          </form>
        </section>
      )}

      {status?.status === "DISABLED" && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <p>
            La bóveda está deshabilitada. Esta situación requiere un procedimiento manual por parte de un
            administrador; no existe una acción de autoservicio para reactivarla.
          </p>
        </section>
      )}

      {status?.initialized && status.status === "ACTIVE" && !status.unlocked && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Desbloquear bóveda</h2>
          <form onSubmit={handleUnlock} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <Input
              label="Contraseña personal de la bóveda"
              type="password"
              required
              value={unlockPassword}
              onChange={(e) => setUnlockPassword(e.target.value)}
            />
            <Button type="submit" disabled={working}>
              Desbloquear
            </Button>
          </form>
        </section>
      )}

      {status?.initialized && status.status === "ACTIVE" && status.unlocked && (
        <>
          <section className="surface-card" style={{ padding: "1.25rem" }}>
            <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Sesión de bóveda</h2>
            <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
              <Button type="button" variant="secondary" onClick={handleLock} disabled={working}>
                Bloquear ahora
              </Button>
              {canRotate && (
                <Button type="button" variant="danger" onClick={handleRotate} disabled={working}>
                  Rotar clave de cifrado
                </Button>
              )}
            </div>
          </section>

          <section className="surface-card" style={{ padding: "1.25rem" }}>
            <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Cambiar contraseña personal</h2>
            <form
              onSubmit={handleChangePassword}
              style={{ display: "flex", flexDirection: "column", gap: "0.75rem", maxWidth: "420px" }}
            >
              <Input
                label="Nueva contraseña personal"
                type="password"
                minLength={VAULT_PASSWORD_MIN_LENGTH}
                maxLength={VAULT_PASSWORD_MAX_LENGTH}
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
              <Button type="submit" disabled={working || !isValidVaultPassword(newPassword)}>
                Cambiar contraseña
              </Button>
            </form>
          </section>

          {canRecover && (
            <section className="surface-card" style={{ padding: "1.25rem" }}>
              <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Código de recuperación</h2>
              {pendingReplacementCode ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                  <p>
                    <strong>Nuevo código de recuperación (única vez que se muestra):</strong>
                  </p>
                  <code
                    style={{
                      padding: "0.6rem",
                      background: "var(--surface-alt, #f4f4f4)",
                      wordBreak: "break-all",
                    }}
                  >
                    {pendingReplacementCode}
                  </code>
                  <p style={{ fontWeight: 600 }}>Guarda el código de recuperación fuera de esta computadora.</p>
                  <Input
                    label="Escribe el código para confirmar que lo guardaste"
                    value={confirmReplacementInput}
                    onChange={(e) => setConfirmReplacementInput(e.target.value)}
                  />
                  <Button
                    type="button"
                    onClick={handleConfirmReplacement}
                    disabled={working || !confirmReplacementInput}
                  >
                    Confirmar nuevo código
                  </Button>
                </div>
              ) : (
                <Button type="button" variant="secondary" onClick={handleReplaceRecovery} disabled={working}>
                  Generar nuevo código de recuperación
                </Button>
              )}
            </section>
          )}

          {canManageUsers && (
            <section className="surface-card" style={{ padding: "1.25rem" }}>
              <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Accesos (slots)</h2>
              <Table
                rows={slots}
                rowKey={(s) => s.id}
                emptyMessage="No hay accesos registrados."
                columns={[
                  { key: "kind", header: "Tipo", render: (s) => (s.kind === "USER" ? "Usuario" : "Recuperación") },
                  {
                    key: "status",
                    header: "Estado",
                    render: (s) => <StatusSemaphore status={s.status} map={SOL_VAULT_SLOT_STATUS_SEMAPHORE} />,
                  },
                  {
                    key: "created_at",
                    header: "Creado",
                    render: (s) => new Date(s.created_at).toLocaleString("es-PE"),
                  },
                  {
                    key: "actions",
                    header: "",
                    render: (s) =>
                      s.status === "ACTIVE" ? (
                        <Button type="button" variant="danger" onClick={() => handleRevokeSlot(s)} disabled={working}>
                          Revocar
                        </Button>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
            </section>
          )}

          {canManageUsers && (
            <section className="surface-card" style={{ padding: "1.25rem" }}>
              <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Invitar a otro usuario</h2>
              <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
                <Select
                  label="Usuario a invitar"
                  placeholder="Selecciona un usuario"
                  options={users
                    .filter((u) => u.id !== user?.id && u.is_active)
                    .map((u) => ({ value: u.id, label: `${u.full_name} (${u.email})` }))}
                  value={inviteTargetUserId}
                  onChange={(e) => setInviteTargetUserId(e.target.value)}
                />
                <Button type="button" onClick={handleCreateInvitation} disabled={working || !inviteTargetUserId}>
                  Invitar
                </Button>
              </div>

              {invitations.length > 0 && (
                <div style={{ marginTop: "1rem" }}>
                  <Table
                    rows={invitations}
                    rowKey={(i) => i.id}
                    columns={[
                      { key: "id", header: "Identificador", render: (i) => <code>{i.id}</code> },
                      {
                        key: "status",
                        header: "Estado",
                        render: (i) => (
                          <StatusSemaphore status={i.status} map={SOL_VAULT_INVITATION_STATUS_SEMAPHORE} />
                        ),
                      },
                      {
                        key: "expires_at",
                        header: "Vence",
                        render: (i) => new Date(i.expires_at).toLocaleString("es-PE"),
                      },
                      {
                        key: "actions",
                        header: "",
                        render: (i) =>
                          i.status === "PENDING" ? (
                            <div style={{ display: "flex", gap: "0.5rem" }}>
                              <Button
                                type="button"
                                variant="secondary"
                                onClick={() => handleReauthorizeInvitation(i)}
                                disabled={working}
                              >
                                Reautorizar
                              </Button>
                              <Button
                                type="button"
                                variant="danger"
                                onClick={() => handleCancelInvitation(i)}
                                disabled={working}
                              >
                                Cancelar
                              </Button>
                            </div>
                          ) : (
                            "—"
                          ),
                      },
                    ]}
                  />
                </div>
              )}
            </section>
          )}
        </>
      )}

      {status?.initialized && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Aceptar una invitación</h2>
          <p style={{ color: "var(--muted)", marginBottom: "0.75rem" }}>
            Pide a la persona que te invitó el identificador de la invitación.
          </p>
          <form
            onSubmit={handleAcceptInvitation}
            style={{ display: "flex", flexDirection: "column", gap: "0.75rem", maxWidth: "420px" }}
          >
            <Input
              label="Identificador de la invitación"
              required
              value={acceptInvitationId}
              onChange={(e) => setAcceptInvitationId(e.target.value)}
            />
            <Input
              label="Elige tu propia contraseña personal de bóveda"
              type="password"
              minLength={VAULT_PASSWORD_MIN_LENGTH}
              maxLength={VAULT_PASSWORD_MAX_LENGTH}
              required
              value={acceptPassword}
              onChange={(e) => setAcceptPassword(e.target.value)}
            />
            <Button type="submit" disabled={working || !isValidVaultPassword(acceptPassword)}>
              Aceptar invitación
            </Button>
          </form>
        </section>
      )}
    </div>
  );
}
