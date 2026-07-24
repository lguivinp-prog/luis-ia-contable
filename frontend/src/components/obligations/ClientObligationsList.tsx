"use client";

import { Button } from "@/components/ui/Button";
import { Table } from "@/components/ui/Table";
import type { ClientObligation } from "@/lib/types/obligation";

interface ClientObligationsListProps {
  obligations: ClientObligation[];
  // El backend identifica la asignacion por obligation_id (FK al catalogo),
  // no por el id propio de la fila client_obligations. Ver app/routers/obligations.py.
  onToggleActive: (obligationId: string, isActive: boolean) => Promise<void>;
  onRemove: (obligationId: string) => Promise<void>;
  canWrite: boolean;
}

export function ClientObligationsList({
  obligations,
  onToggleActive,
  onRemove,
  canWrite,
}: ClientObligationsListProps) {
  const baseColumns = [
    {
      key: "code",
      header: "Código",
      render: (item: ClientObligation) => item.obligation?.code ?? "—",
    },
    {
      key: "name",
      header: "Obligación",
      render: (item: ClientObligation) => item.obligation?.name ?? "—",
    },
    {
      key: "periodicity",
      header: "Periodicidad",
      render: (item: ClientObligation) => item.obligation?.periodicity ?? "—",
    },
    {
      key: "status",
      header: "Estado",
      render: (item: ClientObligation) => (
        <span className={`status-pill ${item.is_active ? "status-verde" : "status-gris"}`}>
          {item.is_active ? "Activa" : "Inactiva"}
        </span>
      ),
    },
  ];

  const actionsColumn = {
    key: "actions",
    header: "Acciones",
    render: (item: ClientObligation) => (
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <Button
          variant="secondary"
          type="button"
          onClick={() => onToggleActive(item.obligation_id, !item.is_active)}
        >
          {item.is_active ? "Desactivar" : "Activar"}
        </Button>
        <Button variant="danger" type="button" onClick={() => onRemove(item.obligation_id)}>
          Quitar
        </Button>
      </div>
    ),
  };

  return (
    <Table
      rows={obligations}
      rowKey={(item) => item.id}
      emptyMessage="Este contribuyente no tiene obligaciones asignadas todavía."
      columns={canWrite ? [...baseColumns, actionsColumn] : baseColumns}
    />
  );
}
