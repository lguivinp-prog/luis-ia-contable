"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { fetchSessionPolicy, keepSessionAlive, logout } from "@/lib/auth";

const ACTIVITY_EVENTS = ["mousemove", "keydown", "click", "touchstart", "scroll"] as const;
const TICK_MS = 1000;

/**
 * Advierte antes de cerrar la sesion por inactividad, con cuenta regresiva y
 * opcion de "Continuar sesion" (POST /auth/keep-alive, protegido con CSRF).
 * No guarda nada en localStorage: solo estado en memoria/timers.
 */
export function InactivityGuard() {
  const router = useRouter();
  const [warningVisible, setWarningVisible] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const lastActivityRef = useRef<number | null>(null);
  const policyRef = useRef<{ timeoutMs: number; warningMs: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    lastActivityRef.current = Date.now();

    fetchSessionPolicy()
      .then((policy) => {
        if (cancelled) return;
        policyRef.current = {
          timeoutMs: policy.inactivity_timeout_minutes * 60 * 1000,
          warningMs: policy.inactivity_warning_seconds * 1000,
        };
      })
      .catch(() => {
        // Sin politica disponible, no se muestra el aviso (evita bloquear la
        // app si la peticion falla).
      });

    function markActivity() {
      lastActivityRef.current = Date.now();
    }

    ACTIVITY_EVENTS.forEach((eventName) => window.addEventListener(eventName, markActivity));

    const interval = setInterval(() => {
      const policy = policyRef.current;
      if (!policy || cancelled || lastActivityRef.current === null) return;

      const idleMs = Date.now() - lastActivityRef.current;
      const timeUntilExpiry = policy.timeoutMs - idleMs;

      if (timeUntilExpiry <= 0) {
        cancelled = true;
        clearInterval(interval);
        logout().finally(() => router.push("/login"));
        return;
      }

      if (timeUntilExpiry <= policy.warningMs) {
        setWarningVisible(true);
        setSecondsLeft(Math.ceil(timeUntilExpiry / 1000));
      } else {
        setWarningVisible(false);
      }
    }, TICK_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
      ACTIVITY_EVENTS.forEach((eventName) => window.removeEventListener(eventName, markActivity));
    };
  }, [router]);

  async function handleContinueSession() {
    try {
      await keepSessionAlive();
    } finally {
      lastActivityRef.current = Date.now();
      setWarningVisible(false);
    }
  }

  if (!warningVisible) {
    return null;
  }

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="inactivity-title"
      aria-describedby="inactivity-description"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: "1rem",
      }}
    >
      <div
        className="surface-card"
        style={{ maxWidth: "420px", width: "100%", padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1rem" }}
      >
        <h2 id="inactivity-title" style={{ fontSize: "1.1rem" }}>
          Tu sesión está por cerrarse
        </h2>
        <p id="inactivity-description" style={{ color: "var(--muted)" }}>
          Por inactividad, tu sesión se cerrará automáticamente en{" "}
          <strong aria-live="polite">{secondsLeft}</strong> segundo{secondsLeft === 1 ? "" : "s"}.
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Button type="button" onClick={handleContinueSession}>
            Continuar sesión
          </Button>
        </div>
      </div>
    </div>
  );
}
