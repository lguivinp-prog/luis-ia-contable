"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import { ApiError } from "@/lib/apiClient";
import {
  changeUserRole,
  createUser,
  deactivateUser,
  listUsers,
  reactivateUser,
  resetUserPassword,
} from "@/lib/users";
import type { ManagedUser, Role } from "@/lib/types/user";

const ROLE_OPTIONS = [
  { value: "admin", label: "Administrador" },
  { value: "contador", label: "Contador" },
  { value: "asistente", label: "Asistente" },
];

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 409) {
      return typeof err.detail === "string"
        ? err.detail
        : "Conflicto: la operación no puede completarse en el estado actual (motivo/último administrador/correo duplicado).";
    }
    if (err.status === 404) {
      return "Este usuario ya no existe. Recarga la lista.";
    }
    if (err.status === 422) {
      return typeof err.detail === "string" ? err.detail : "Datos inválidos. Revisa los campos.";
    }
    if (err.status === 403) {
      return "No tienes permiso para realizar esta acción.";
    }
  }
  return fallback;
}

export default function UsersPage() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // alta
  const [newEmail, setNewEmail] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newRole, setNewRole] = useState<Role>("contador");
  const [newPassword, setNewPassword] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // acciones sobre el usuario seleccionado
  const [roleTarget, setRoleTarget] = useState<Role>("contador");
  const [roleReason, setRoleReason] = useState("");
  const [deactivateReason, setDeactivateReason] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [resetReason, setResetReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSubmitting, setActionSubmitting] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (err) {
      setLoadError(
        err instanceof ApiError && err.status === 403
          ? "No tienes permiso para ver la gestión de usuarios."
          : "No se pudieron cargar los usuarios.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Misma forma que clients/page.tsx (.then/.finally directo, sin invocar
    // una funcion async dentro del cuerpo del efecto) para evitar cascadas de
    // renders sincronas. `loading` ya inicia en true (useState(true)), no
    // hace falta volver a fijarlo de forma sincrona aqui.
    listUsers()
      .then(setUsers)
      .catch((err) => {
        setLoadError(
          err instanceof ApiError && err.status === 403
            ? "No tienes permiso para ver la gestión de usuarios."
            : "No se pudieron cargar los usuarios.",
        );
      })
      .finally(() => setLoading(false));
  }, []);

  const selectedUser = users.find((u) => u.id === selectedId) ?? null;

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError(null);
    setCreating(true);
    try {
      await createUser({
        email: newEmail,
        full_name: newFullName,
        role: newRole,
        password: newPassword,
      });
      setNewEmail("");
      setNewFullName("");
      setNewRole("contador");
      setNewPassword("");
      await reload();
    } catch (err) {
      setCreateError(describeError(err, "No se pudo crear el usuario."));
    } finally {
      setCreating(false);
    }
  }

  async function handleChangeRole() {
    if (!selectedUser) return;
    if (!roleReason.trim()) {
      setActionError("El cambio de rol requiere un motivo.");
      return;
    }
    const confirmed = window.confirm(
      `¿Confirmas cambiar el rol de ${selectedUser.email} a "${roleTarget}"? Esta acción queda auditada.`,
    );
    if (!confirmed) return;
    setActionError(null);
    setActionSubmitting(true);
    try {
      await changeUserRole(selectedUser.id, roleTarget, roleReason);
      setRoleReason("");
      await reload();
    } catch (err) {
      setActionError(describeError(err, "No se pudo cambiar el rol."));
    } finally {
      setActionSubmitting(false);
    }
  }

  async function handleDeactivate() {
    if (!selectedUser) return;
    if (!deactivateReason.trim()) {
      setActionError("La desactivación requiere un motivo.");
      return;
    }
    const confirmed = window.confirm(
      `¿Confirmas DESACTIVAR a ${selectedUser.email}? Se cerrarán todas sus sesiones activas de inmediato.`,
    );
    if (!confirmed) return;
    setActionError(null);
    setActionSubmitting(true);
    try {
      await deactivateUser(selectedUser.id, deactivateReason);
      setDeactivateReason("");
      await reload();
    } catch (err) {
      setActionError(describeError(err, "No se pudo desactivar el usuario."));
    } finally {
      setActionSubmitting(false);
    }
  }

  async function handleReactivate() {
    if (!selectedUser) return;
    const confirmed = window.confirm(`¿Confirmas reactivar a ${selectedUser.email}?`);
    if (!confirmed) return;
    setActionError(null);
    setActionSubmitting(true);
    try {
      await reactivateUser(selectedUser.id);
      await reload();
    } catch (err) {
      setActionError(describeError(err, "No se pudo reactivar el usuario."));
    } finally {
      setActionSubmitting(false);
    }
  }

  async function handleResetPassword() {
    if (!selectedUser) return;
    if (resetPassword.length < 8) {
      setActionError("La nueva contraseña debe tener al menos 8 caracteres.");
      return;
    }
    if (!resetReason.trim()) {
      setActionError("El restablecimiento de contraseña requiere un motivo.");
      return;
    }
    const confirmed = window.confirm(
      `¿Confirmas restablecer la contraseña de ${selectedUser.email}? Se cerrarán todas sus sesiones activas y deberá cambiarla al iniciar sesión.`,
    );
    if (!confirmed) return;
    setActionError(null);
    setActionSubmitting(true);
    try {
      await resetUserPassword(selectedUser.id, resetPassword, resetReason);
      setResetPassword("");
      setResetReason("");
      await reload();
    } catch (err) {
      setActionError(describeError(err, "No se pudo restablecer la contraseña."));
    } finally {
      setActionSubmitting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <div>
        <h1 style={{ fontSize: "1.4rem", marginBottom: "1rem" }}>Usuarios</h1>

        {loadError && <div className="error-banner">{loadError}</div>}

        {loading ? (
          <p>Cargando…</p>
        ) : (
          <Table
            rows={users}
            rowKey={(u) => u.id}
            emptyMessage="No hay usuarios."
            columns={[
              { key: "email", header: "Correo", render: (u) => u.email },
              { key: "full_name", header: "Nombre", render: (u) => u.full_name },
              {
                key: "role",
                header: "Rol",
                render: (u) => ROLE_OPTIONS.find((r) => r.value === u.role)?.label ?? u.role,
              },
              {
                key: "status",
                header: "Estado",
                render: (u) => (
                  <span className={`badge ${u.is_active ? "" : "status-pill status-naranja"}`}>
                    {u.is_active ? "Activo" : "Inactivo"}
                  </span>
                ),
              },
              {
                key: "must_change_password",
                header: "Contraseña",
                render: (u) =>
                  u.must_change_password ? (
                    <span className="status-pill status-amarillo">Cambio pendiente</span>
                  ) : (
                    "—"
                  ),
              },
              {
                key: "last_login_at",
                header: "Último login",
                render: (u) => (u.last_login_at ? new Date(u.last_login_at).toLocaleString("es-PE") : "—"),
              },
              {
                key: "actions",
                header: "",
                render: (u) => (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setSelectedId(u.id === selectedId ? null : u.id);
                      setRoleTarget(u.role);
                      setActionError(null);
                    }}
                  >
                    {u.id === selectedId ? "Ocultar" : "Gestionar"}
                  </Button>
                ),
              },
            ]}
          />
        )}
      </div>

      {selectedUser && (
        <div className="surface-card" style={{ padding: "1.25rem", maxWidth: "480px" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Gestionar a {selectedUser.email}</h2>
          {actionError && <div className="error-banner">{actionError}</div>}

          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginBottom: "1.5rem" }}>
            <Select
              label="Nuevo rol"
              options={ROLE_OPTIONS}
              value={roleTarget}
              onChange={(e) => setRoleTarget(e.target.value as Role)}
            />
            <Input
              label="Motivo del cambio de rol"
              value={roleReason}
              onChange={(e) => setRoleReason(e.target.value)}
            />
            <Button type="button" onClick={handleChangeRole} disabled={actionSubmitting}>
              Cambiar rol
            </Button>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginBottom: "1.5rem" }}>
            {selectedUser.is_active ? (
              <>
                <Input
                  label="Motivo de desactivación"
                  value={deactivateReason}
                  onChange={(e) => setDeactivateReason(e.target.value)}
                />
                <Button
                  type="button"
                  variant="danger"
                  onClick={handleDeactivate}
                  disabled={actionSubmitting}
                >
                  Desactivar usuario
                </Button>
              </>
            ) : (
              <Button type="button" onClick={handleReactivate} disabled={actionSubmitting}>
                Reactivar usuario
              </Button>
            )}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <Input
              label="Nueva contraseña temporal"
              type="password"
              minLength={8}
              value={resetPassword}
              onChange={(e) => setResetPassword(e.target.value)}
            />
            <Input
              label="Motivo del restablecimiento"
              value={resetReason}
              onChange={(e) => setResetReason(e.target.value)}
            />
            <Button
              type="button"
              variant="danger"
              onClick={handleResetPassword}
              disabled={actionSubmitting}
            >
              Restablecer contraseña
            </Button>
          </div>
        </div>
      )}

      <div className="surface-card" style={{ padding: "1.25rem", maxWidth: "420px" }}>
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Nuevo usuario</h2>
        <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          {createError && <div className="error-banner">{createError}</div>}
          <Input
            label="Correo"
            type="email"
            required
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
          />
          <Input
            label="Nombre completo"
            required
            value={newFullName}
            onChange={(e) => setNewFullName(e.target.value)}
          />
          <Select
            label="Rol"
            options={ROLE_OPTIONS}
            value={newRole}
            onChange={(e) => setNewRole(e.target.value as Role)}
          />
          <Input
            label="Contraseña inicial (temporal)"
            type="password"
            required
            minLength={8}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <Button type="submit" disabled={creating}>
            {creating ? "Creando…" : "Crear usuario"}
          </Button>
        </form>
      </div>
    </div>
  );
}
