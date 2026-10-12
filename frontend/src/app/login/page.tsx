"use client";

import { SecureForm } from "@/components/ui/SecureForm";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { fetchCurrentUser, login } from "@/lib/auth";
import { safeNextPath } from "@/lib/authRedirect";
import { describeLoginError } from "@/lib/loginError";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function destination(): string {
    // el destino viaja en `?next=` y solo se acepta una ruta interna segura (nunca una URL externa)
    return safeNextPath(new URLSearchParams(window.location.search).get("next")) ?? "/clients";
  }

  // Un enlace abierto desde otro sitio llega sin cookie (SameSite=Strict) aunque la sesion siga vigente: esta consulta es una peticion del
  // propio sitio, que SI lleva la cookie. Si la sesion es valida se vuelve al destino sin pedir otra vez las credenciales.
  useEffect(() => {
    let alive = true;
    fetchCurrentUser()
      .then(() => {
        if (alive) router.replace(destination());
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.push(destination());
      router.refresh();
    } catch (err) {
      setError(describeLoginError(err));
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
      <SecureForm
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
      </SecureForm>
    </div>
  );
}
