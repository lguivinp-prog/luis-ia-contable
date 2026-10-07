"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { ClientForm } from "@/components/clients/ClientForm";
import { apiClient } from "@/lib/apiClient";
import { buildClientUpdatePayload } from "@/lib/clientUpdatePayload";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import { canViewSupervisedSolAccess } from "@/lib/solAccessPermissions";
import { canReadSolCredential } from "@/lib/solVaultPermissions";
import type { Client, ClientBase, ServiceStatus } from "@/lib/types/client";

const SERVICE_STATUS_OPTIONS = [
  { value: "prospecto", label: "Prospecto" },
  { value: "activo", label: "Activo" },
  { value: "suspendido", label: "Suspendido" },
  { value: "pendiente_cierre", label: "Pendiente de cierre" },
  { value: "inactivo", label: "Inactivo" },
];

export default function ClientDetailPage() {
  const params = useParams<{ id: string }>();
  const { user } = useCurrentUser();
  const canWrite = hasPermission(user, "client:write");
  const [client, setClient] = useState<Client | null>(null);
  const [newStatus, setNewStatus] = useState<ServiceStatus | "">("");
  const [statusNote, setStatusNote] = useState("");
  const [statusError, setStatusError] = useState<string | null>(null);

  useEffect(() => {
    apiClient.get<Client>(`/clients/${params.id}`).then(setClient);
  }, [params.id]);

  async function handleSubmit(values: ClientBase) {
    const payload = buildClientUpdatePayload(values);
    const updated = await apiClient.put<Client>(`/clients/${params.id}`, payload);
    setClient(updated);
  }

  async function handleStatusChange() {
    setStatusError(null);
    if (!newStatus || !statusNote.trim()) {
      setStatusError("Selecciona un estado y escribe una nota explicando el cambio.");
      return;
    }
    try {
      const updated = await apiClient.patch<Client>(`/clients/${params.id}/status`, {
        service_status: newStatus,
        note: statusNote,
      });
      setClient(updated);
      setNewStatus("");
      setStatusNote("");
    } catch {
      setStatusError("No se pudo actualizar el estado.");
    }
  }

  if (!client) {
    return <p>Cargando…</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem", maxWidth: "720px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ fontSize: "1.4rem" }}>{client.business_name}</h1>
          <p style={{ color: "var(--muted)" }}>RUC {client.ruc}</p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <Link href={`/clients/${client.id}/obligations`}>
            <Button variant="secondary" type="button">
              Ver obligaciones
            </Button>
          </Link>
          <Link href={`/clients/${client.id}/documents`}>
            <Button variant="secondary" type="button">
              Requisitos documentales
            </Button>
          </Link>
          <Link href={`/clients/${client.id}/fees`}>
            <Button variant="secondary" type="button">
              Honorarios
            </Button>
          </Link>
          {canReadSolCredential(user) && (
            <Link href={`/clients/${client.id}/sol-credential`}>
              <Button variant="secondary" type="button">
                Credencial SOL
              </Button>
            </Link>
          )}
          {canViewSupervisedSolAccess(user) && (
            <Link href={`/clients/${client.id}/sol-access`}>
              <Button variant="secondary" type="button">
                Acceso SOL supervisado
              </Button>
            </Link>
          )}
        </div>
      </div>

      {canWrite && (
        <section className="surface-card" style={{ padding: "1.25rem" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Cambiar estado de servicio</h2>
          {statusError && (
            <div className="error-banner" style={{ marginBottom: "0.75rem" }}>{statusError}</div>
          )}
          <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
            <Select
              label="Nuevo estado"
              name="newStatus"
              placeholder="Selecciona"
              options={SERVICE_STATUS_OPTIONS}
              value={newStatus}
              onChange={(event) => setNewStatus(event.target.value as ServiceStatus)}
            />
            <div className="field" style={{ flex: 1, minWidth: "220px" }}>
              <label htmlFor="statusNote">Motivo del cambio</label>
              <input
                id="statusNote"
                className="input"
                value={statusNote}
                onChange={(event) => setStatusNote(event.target.value)}
              />
            </div>
            <Button type="button" onClick={handleStatusChange}>
              Actualizar estado
            </Button>
          </div>
        </section>
      )}

      <section>
        <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Datos del contribuyente</h2>
        {canWrite ? (
          <ClientForm initialValues={client} onSubmit={handleSubmit} submitLabel="Guardar cambios" lockRuc />
        ) : (
          <p style={{ color: "var(--muted)" }}>
            No tienes permiso para editar contribuyentes. Contacta a un administrador o contador.
          </p>
        )}
      </section>
    </div>
  );
}
