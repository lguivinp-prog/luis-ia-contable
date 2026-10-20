"use client";

// Expediente de CONTROL SIRE del cliente y periodo seleccionados (pestaña SIRE del workspace mensual): dos secciones independientes, RVIE y RCE. Consultar nunca crea
// expedientes; solo la primera incorporacion de una fuente o archivo lo hace. No presenta, no paga y no cambia ningun estado tributario.
import { useCallback, useEffect, useState } from "react";

import { SireComponentSection } from "@/components/sire/SireComponentSection";
import { useCurrentUser } from "@/lib/currentUserContext";
import { fetchControlsForPeriod } from "@/lib/sireControl";
import { OFFICIAL_FILING_NOTICE, canReadSireControl, canWriteSireControl } from "@/lib/sireControlModel";
import type { SireControlsForPeriod } from "@/lib/types/sireControl";

interface Props {
  clientId: string;
  periodId: string;
}

export function SireControlPanel({ clientId, periodId }: Props) {
  const { user } = useCurrentUser();
  const canRead = canReadSireControl(user);
  const canWrite = canWriteSireControl(user);
  const [data, setData] = useState<SireControlsForPeriod | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!periodId) {
      setLoading(false);
      return;
    }
    try {
      setData(await fetchControlsForPeriod(clientId, periodId));
      setError(null);
    } catch {
      setError("No se pudo cargar el control SIRE. Inténtalo de nuevo.");
    } finally {
      setLoading(false);
    }
  }, [clientId, periodId]);

  useEffect(() => {
    if (!canRead) return;
    void Promise.resolve().then(() => load());
  }, [canRead, load]);

  if (!canRead) return null;
  return (
    <section aria-labelledby="sire-control-title" data-testid="sire-control-panel" style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <div>
        <h2 id="sire-control-title" style={{ fontSize: "1.15rem", margin: 0 }}>Control y conciliación del SIRE</h2>
        <p style={{ color: "var(--muted)", fontSize: "0.85rem", margin: "0.25rem 0 0" }}>
          Compara lo informado por SUNAT con los resultados de DARWIN / CONTASIS, registra tu revisión y conserva la evidencia. No presenta registros ni cambia estados tributarios.
        </p>
        <p role="note" style={{ color: "var(--muted)", fontSize: "0.8rem", margin: "0.25rem 0 0" }}>{OFFICIAL_FILING_NOTICE}</p>
      </div>
      {loading && <p role="status">Cargando control SIRE…</p>}
      {error && <div role="alert" className="simulated-banner" style={{ borderColor: "var(--danger, #c0392b)" }}>{error}</div>}
      {!periodId && <p style={{ color: "var(--muted)" }}>Selecciona un periodo para ver el control SIRE.</p>}
      {data && data.controls.map((control) => (
        <SireComponentSection key={control.component} control={control} clientId={clientId} periodId={periodId} canWrite={canWrite} onChanged={() => void load()} />
      ))}
    </section>
  );
}
