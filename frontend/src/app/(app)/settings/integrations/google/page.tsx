"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { useCurrentUser } from "@/lib/currentUserContext";
import { describeGoogleError } from "@/lib/googleIntegrationErrors";
import { disconnectGoogle, fetchGoogleStatus, startGoogleAuthorization } from "@/lib/integrations";
import { hasPermission } from "@/lib/permissions";
import { ApiError } from "@/lib/apiClient";
import type { GoogleConnectionStatus } from "@/lib/types/integrations";

const RETURN_PATH = "/settings/integrations/google";

function GoogleIntegrationContent() {
  const { user } = useCurrentUser();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [status, setStatus] = useState<GoogleConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [working, setWorking] = useState(false);

  const canConnect = hasPermission(user, "integration:connect");
  const canDisconnect = hasPermission(user, "integration:disconnect");

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchGoogleStatus();
      setStatus(data);
    } catch {
      setError("No se pudo cargar el estado de la integración.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  useEffect(() => {
    void Promise.resolve().then(() => {
      const googleStatus = searchParams.get("google_status");
      const googleError = searchParams.get("google_error");
      if (googleStatus === "connected") {
        setBanner("Cuenta de Google conectada correctamente.");
        router.replace(RETURN_PATH);
      } else if (googleError) {
        setBanner(describeGoogleError(googleError));
        router.replace(RETURN_PATH);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  async function handleConnect() {
    setWorking(true);
    setError(null);
    try {
      const { authorization_url } = await startGoogleAuthorization(RETURN_PATH);
      window.location.href = authorization_url;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("La integración con Google no está configurada en este entorno.");
      } else {
        setError("No se pudo iniciar la conexión con Google.");
      }
      setWorking(false);
    }
  }

  async function handleDisconnect() {
    setWorking(true);
    setError(null);
    try {
      const result = await disconnectGoogle();
      setBanner(
        result.revocation_succeeded
          ? "Cuenta de Google desconectada."
          : "Cuenta de Google desconectada localmente (no se pudo confirmar la revocación remota)."
      );
      setConfirmingDisconnect(false);
      await reload();
    } catch {
      setError("No se pudo desconectar la cuenta de Google.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "700px" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Integración con Google</h1>

      {banner && <div className="simulated-banner">{banner}</div>}
      {error && <div className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>{error}</div>}

      {loading ? (
        <p>Cargando…</p>
      ) : !canConnect ? (
        <p>No tienes permiso para ver esta integración.</p>
      ) : status === null ? (
        <p>No se pudo determinar el estado de la integración.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <strong>Estado: </strong>
            {status.connected ? (
              <span>
                Conectado{status.google_account_email ? ` (${status.google_account_email})` : ""}
                {status.status === "NEEDS_REAUTH" && " — requiere reautorización"}
              </span>
            ) : (
              <span>No conectado</span>
            )}
          </div>

          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            <li>
              {status.identity_authorized ? "✅" : "⬜"} Identidad verificada
            </li>
            <li style={{ opacity: 0.6 }}>⬜ Calendario autorizado (próximamente)</li>
            <li style={{ opacity: 0.6 }}>⬜ Drive autorizado (próximamente)</li>
          </ul>

          {!status.configured ? (
            <p style={{ opacity: 0.8 }}>
              Google no configurado en este entorno. Un administrador debe registrar las
              credenciales OAuth reales antes de poder conectar una cuenta.
            </p>
          ) : !status.connected || status.status === "NEEDS_REAUTH" ? (
            <Button type="button" onClick={handleConnect} disabled={working || !canConnect}>
              {status.status === "NEEDS_REAUTH" ? "Reconectar con Google" : "Conectar con Google"}
            </Button>
          ) : confirmingDisconnect ? (
            <div style={{ display: "flex", gap: "0.75rem", alignItems: "center" }}>
              <span>¿Confirmas desconectar la cuenta de Google?</span>
              <Button type="button" variant="danger" onClick={handleDisconnect} disabled={working || !canDisconnect}>
                Sí, desconectar
              </Button>
              <Button type="button" variant="secondary" onClick={() => setConfirmingDisconnect(false)} disabled={working}>
                Cancelar
              </Button>
            </div>
          ) : (
            <Button
              type="button"
              variant="danger"
              onClick={() => setConfirmingDisconnect(true)}
              disabled={working || !canDisconnect}
            >
              Desconectar
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export default function GoogleIntegrationPage() {
  return (
    <Suspense fallback={<p>Cargando…</p>}>
      <GoogleIntegrationContent />
    </Suspense>
  );
}
