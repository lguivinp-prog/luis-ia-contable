"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Table } from "@/components/ui/Table";
import { DOCUMENT_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import { ApiError } from "@/lib/apiClient";
import type {
  ClientDocumentRequirement,
  DeclarationDocumentItem,
  DocumentItemStatus,
  DocumentRequirementCatalog,
  SyncRequirementsResult,
} from "@/lib/types/declarationDocument";
import type { Declaration } from "@/lib/types/declaration";

const ITEM_STATUS_OPTIONS: { value: DocumentItemStatus; label: string }[] = [
  { value: "NOT_REQUESTED", label: "No solicitado" },
  { value: "REQUESTED", label: "Solicitado" },
  { value: "PARTIAL", label: "Recibido parcialmente" },
  { value: "RECEIVED", label: "Recibido" },
  { value: "OBSERVED", label: "Observado" },
  { value: "ACCEPTED", label: "Aceptado" },
  { value: "NOT_APPLICABLE", label: "No aplica" },
];

interface DocumentsPanelProps {
  declaration: Declaration;
  items: DeclarationDocumentItem[];
  requirements: ClientDocumentRequirement[];
  catalog: DocumentRequirementCatalog[];
  periodIsOpen: boolean;
  canManage: boolean;
  canWriteItems: boolean;
  onSync: () => Promise<SyncRequirementsResult>;
  onChangeStatus: (itemId: string, target: DocumentItemStatus, reason: string | null) => Promise<void>;
}

export function DocumentsPanel({
  declaration,
  items,
  requirements,
  catalog,
  periodIsOpen,
  canManage,
  canWriteItems,
  onSync,
  onChangeStatus,
}: DocumentsPanelProps) {
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncRequirementsResult | null>(null);
  const [pendingTargets, setPendingTargets] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busyItemId, setBusyItemId] = useState<string | null>(null);

  const requirementsById = new Map(requirements.map((r) => [r.id, r]));
  const catalogById = new Map(catalog.map((c) => [c.id, c]));

  const periodAndStatusAllow =
    periodIsOpen && !["CLOSED", "CANCELLED"].includes(declaration.workflow_status);
  const canSync = periodAndStatusAllow && canManage;
  const canAct = periodAndStatusAllow && canWriteItems;

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      const result = await onSync();
      setSyncResult(result);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo sincronizar los requisitos.");
    } finally {
      setSyncing(false);
    }
  }

  async function handleApply(itemId: string) {
    const target = pendingTargets[itemId] as DocumentItemStatus | undefined;
    if (!target) return;
    let reason: string | null = null;
    if (target === "OBSERVED") {
      reason = window.prompt("Motivo de la observación (opcional):");
    }
    setBusyItemId(itemId);
    setError(null);
    try {
      await onChangeStatus(itemId, target, reason);
      setPendingTargets((prev) => ({ ...prev, [itemId]: "" }));
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cambiar el estado del item.");
    } finally {
      setBusyItemId(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
        <strong>Estado documental:</strong>
        <StatusSemaphore status={declaration.document_status} map={DOCUMENT_STATUS_SEMAPHORE} />
        {canManage && (
          <Button type="button" variant="secondary" onClick={handleSync} disabled={syncing || !canSync}>
            {syncing ? "Sincronizando…" : "Sincronizar requisitos"}
          </Button>
        )}
      </div>

      {!canAct && (
        <p style={{ color: "var(--muted)" }}>
          {periodIsOpen
            ? "La declaración está cerrada o cancelada: no se pueden modificar documentos."
            : "El periodo tributario está cerrado: no se pueden modificar documentos."}
        </p>
      )}

      {syncResult && (
        <p style={{ color: "var(--muted)" }}>
          Sincronización: {syncResult.created.length} creado(s), {syncResult.existing.length} ya
          existente(s)
          {syncResult.errors.length > 0 ? `, ${syncResult.errors.length} con error` : ""}.
        </p>
      )}

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {items.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>
          Todavía no hay requisitos documentales materializados para esta declaración. Asigna
          requisitos al cliente y usa &quot;Sincronizar requisitos&quot;.
        </p>
      ) : (
        <Table
          rows={items}
          rowKey={(item) => item.id}
          columns={[
            {
              key: "name",
              header: "Requisito",
              render: (item) => {
                const requirement = requirementsById.get(item.client_document_requirement_id);
                const catalogEntry = requirement ? catalogById.get(requirement.requirement_catalog_id) : undefined;
                return catalogEntry?.name ?? item.client_document_requirement_id;
              },
            },
            {
              key: "scope",
              header: "Vínculo",
              render: (item) => {
                const requirement = requirementsById.get(item.client_document_requirement_id);
                return requirement?.client_obligation_id ? "Obligación específica" : "General del cliente";
              },
            },
            {
              key: "status",
              header: "Estado",
              render: (item) => (
                <span className={`status-pill ${item.status === "OBSERVED" ? "status-rojo" : "status-gris"}`}>
                  {ITEM_STATUS_OPTIONS.find((o) => o.value === item.status)?.label ?? item.status}
                </span>
              ),
            },
            {
              key: "dates",
              header: "Fechas",
              render: (item) => (
                <span style={{ fontSize: "0.85rem" }}>
                  {item.requested_at ? `Solicitado: ${item.requested_at.slice(0, 10)}` : ""}
                  {item.received_at ? ` · Recibido: ${item.received_at.slice(0, 10)}` : ""}
                  {item.reviewed_at ? ` · Revisado: ${item.reviewed_at.slice(0, 10)}` : ""}
                  {!item.requested_at && !item.received_at && !item.reviewed_at ? "—" : ""}
                </span>
              ),
            },
            {
              key: "observations",
              header: "Observaciones",
              render: (item) => item.observations ?? "—",
            },
            {
              key: "actions",
              header: "Cambiar estado",
              render: (item) => (
                <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                  <select
                    aria-label={`Nuevo estado para ${item.id}`}
                    className="select"
                    disabled={!canAct}
                    value={pendingTargets[item.id] ?? ""}
                    onChange={(e) =>
                      setPendingTargets((prev) => ({ ...prev, [item.id]: e.target.value }))
                    }
                  >
                    <option value="">Selecciona…</option>
                    {ITEM_STATUS_OPTIONS.filter((o) => o.value !== item.status).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!canAct || !pendingTargets[item.id] || busyItemId === item.id}
                    onClick={() => handleApply(item.id)}
                  >
                    {busyItemId === item.id ? "Aplicando…" : "Aplicar"}
                  </Button>
                </div>
              ),
            },
          ]}
        />
      )}
    </div>
  );
}
