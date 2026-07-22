import Link from "next/link";

import { Table } from "@/components/ui/Table";
import { SERVICE_STATUS_SEMAPHORE, StatusSemaphore } from "@/components/ui/StatusSemaphore";
import type { Client } from "@/lib/types/client";

export function ClientsTable({ clients }: { clients: Client[] }) {
  return (
    <Table
      rows={clients}
      rowKey={(client) => client.id}
      emptyMessage="No hay contribuyentes que coincidan con los filtros."
      columns={[
        {
          key: "ruc",
          header: "RUC",
          render: (client) => client.ruc,
        },
        {
          key: "business_name",
          header: "Razón social",
          render: (client) => (
            <Link href={`/clients/${client.id}`} style={{ fontWeight: 600 }}>
              {client.business_name}
            </Link>
          ),
        },
        {
          key: "tax_regime",
          header: "Régimen",
          render: (client) => client.tax_regime,
        },
        {
          key: "monthly_fee",
          header: "Honorario",
          render: (client) => (client.monthly_fee ? `S/ ${client.monthly_fee}` : "—"),
        },
        {
          key: "service_status",
          header: "Estado",
          render: (client) => (
            <StatusSemaphore status={client.service_status} map={SERVICE_STATUS_SEMAPHORE} />
          ),
        },
      ]}
    />
  );
}
