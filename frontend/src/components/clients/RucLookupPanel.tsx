"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { apiClient, ApiError } from "@/lib/apiClient";
import type { RucLookupResult } from "@/lib/types/client";
import { isValidRuc } from "@/lib/validators/ruc";

interface RucLookupPanelProps {
  ruc: string;
  onResult: (result: RucLookupResult) => void;
}

export function RucLookupPanel({ ruc, onResult }: RucLookupPanelProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<RucLookupResult | null>(null);

  async function handleLookup() {
    setError(null);
    setLoading(true);
    try {
      const result = await apiClient.get<RucLookupResult>(`/clients/ruc-lookup/${ruc}`);
      setLastResult(result);
      onResult(result);
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        setError("El proveedor real de consulta de RUC aún no está implementado (ver docs/ROADMAP.md).");
      } else {
        setError("No se pudo consultar el RUC. Verifica el formato.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      <Button type="button" variant="secondary" disabled={!isValidRuc(ruc) || loading} onClick={handleLookup}>
        {loading ? "Consultando…" : "Consultar RUC (proveedor simulado)"}
      </Button>
      {error && <div className="error-banner">{error}</div>}
      {lastResult?.is_simulated && (
        <div className="simulated-banner">
          Estos datos provienen de un proveedor <strong>simulado</strong> de desarrollo. Revísalos antes de guardar.
        </div>
      )}
    </div>
  );
}
