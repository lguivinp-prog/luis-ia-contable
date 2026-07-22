"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ApiError } from "@/lib/apiClient";
import { login } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.push("/clients");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Correo o contraseña incorrectos.");
      } else {
        setError("No se pudo iniciar sesión. Intenta nuevamente.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--background)",
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="surface-card"
        style={{ width: "360px", padding: "2rem", display: "flex", flexDirection: "column", gap: "1rem" }}
      >
        <div>
          <h1 style={{ fontSize: "1.3rem", marginBottom: "0.25rem" }}>Luis IA Contable</h1>
          <p style={{ color: "var(--muted)", fontSize: "0.88rem" }}>Ingresa con tu cuenta de administrador.</p>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <Input
          label="Correo electrónico"
          name="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Input
          label="Contraseña"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        <Button type="submit" disabled={submitting}>
          {submitting ? "Ingresando…" : "Ingresar"}
        </Button>
      </form>
    </div>
  );
}
