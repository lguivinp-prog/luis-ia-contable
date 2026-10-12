"use client";

import { SecureForm } from "@/components/ui/SecureForm";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { changePassword } from "@/lib/auth";
import { ApiError } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      return "La contraseña actual no es correcta.";
    }
    if (err.status === 422) {
      return "La nueva contraseña no cumple los requisitos mínimos (al menos 8 caracteres).";
    }
  }
  return "No se pudo cambiar la contraseña. Intenta nuevamente.";
}

export default function ChangePasswordRequiredPage() {
  const { user, refreshCurrentUser } = useCurrentUser();
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!user?.must_change_password) {
    return (
      <div style={{ maxWidth: "420px" }}>
        <p>No tienes un cambio de contraseña pendiente.</p>
      </div>
    );
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (newPassword !== confirmPassword) {
      setError("La confirmación no coincide con la nueva contraseña.");
      return;
    }

    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      await refreshCurrentUser();
      router.replace("/dashboard");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ maxWidth: "420px" }}>
      <h1 style={{ fontSize: "1.4rem", marginBottom: "1rem" }}>Cambio de contraseña obligatorio</h1>
      <p style={{ color: "var(--muted)", marginBottom: "1rem" }}>
        Un administrador te asignó una contraseña temporal. Debes establecer una contraseña propia antes
        de continuar.
      </p>
      <SecureForm onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        {error && <div className="error-banner">{error}</div>}
        <Input
          label="Contraseña actual (temporal)"
          type="password"
          name="current_password"
          required
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
        />
        <Input
          label="Nueva contraseña"
          type="password"
          name="new_password"
          required
          minLength={8}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
        />
        <Input
          label="Confirmar nueva contraseña"
          type="password"
          name="confirm_password"
          required
          minLength={8}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
        />
        <Button type="submit" disabled={submitting}>
          {submitting ? "Guardando…" : "Cambiar contraseña"}
        </Button>
      </SecureForm>
    </div>
  );
}
