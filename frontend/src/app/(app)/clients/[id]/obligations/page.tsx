"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ClientObligationsList } from "@/components/obligations/ClientObligationsList";
import { ObligationCatalogPicker } from "@/components/obligations/ObligationCatalogPicker";
import { apiClient } from "@/lib/apiClient";
import type { ClientObligation, ObligationCatalogItem } from "@/lib/types/obligation";

export default function ClientObligationsPage() {
  const params = useParams<{ id: string }>();
  const clientId = params.id;

  const [catalog, setCatalog] = useState<ObligationCatalogItem[]>([]);
  const [assigned, setAssigned] = useState<ClientObligation[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const [catalogData, assignedData] = await Promise.all([
      apiClient.get<ObligationCatalogItem[]>("/obligations/catalog"),
      apiClient.get<ClientObligation[]>(`/clients/${clientId}/obligations`),
    ]);
    setCatalog(catalogData);
    setAssigned(assignedData);
    setLoading(false);
  }, [clientId]);

  useEffect(() => {
    // Se difiere a un microtask para no invocar setState de forma sincrona
    // dentro del cuerpo del efecto (regla react-hooks/set-state-in-effect).
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleAssign(obligationId: string) {
    await apiClient.post(`/clients/${clientId}/obligations`, { obligation_id: obligationId });
    await reload();
  }

  async function handleToggleActive(obligationId: string, isActive: boolean) {
    await apiClient.patch(`/clients/${clientId}/obligations/${obligationId}`, { is_active: isActive });
    await reload();
  }

  async function handleRemove(obligationId: string) {
    await apiClient.delete(`/clients/${clientId}/obligations/${obligationId}`);
    await reload();
  }

  const catalogById = new Map(catalog.map((item) => [item.id, item]));
  // El backend no expande la relacion "obligation" en la respuesta (ver
  // ClientObligationOut en app/schemas/obligation.py); se enriquece aqui
  // cruzando con el catalogo ya cargado para mostrar codigo/nombre/periodicidad.
  const assignedWithCatalog = assigned.map((item) => ({
    ...item,
    obligation: item.obligation ?? catalogById.get(item.obligation_id) ?? null,
  }));

  const assignedObligationIds = new Set(assigned.map((item) => item.obligation_id));
  const availableObligations = catalog.filter((item) => !assignedObligationIds.has(item.id));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "780px" }}>
      <div>
        <Link href={`/clients/${clientId}`} style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          ← Volver al contribuyente
        </Link>
        <h1 style={{ fontSize: "1.4rem", marginTop: "0.4rem" }}>Obligaciones del contribuyente</h1>
      </div>

      {loading ? (
        <p>Cargando…</p>
      ) : (
        <>
          <ObligationCatalogPicker availableObligations={availableObligations} onAssign={handleAssign} />
          <ClientObligationsList
            obligations={assignedWithCatalog}
            onToggleActive={handleToggleActive}
            onRemove={handleRemove}
          />
        </>
      )}
    </div>
  );
}
