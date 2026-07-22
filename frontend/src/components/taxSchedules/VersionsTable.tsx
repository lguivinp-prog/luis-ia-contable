import Link from "next/link";

import { Table } from "@/components/ui/Table";
import type { TaxScheduleVersion } from "@/lib/types/taxSchedule";

const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Borrador",
  IMPORTED: "Importada",
  PENDING_REVIEW: "En revisión",
  APPROVED: "Aprobada",
  SUPERSEDED: "Reemplazada",
  REJECTED: "Rechazada",
  CANCELLED: "Cancelada",
};

export function VersionsTable({ versions }: { versions: TaxScheduleVersion[] }) {
  return (
    <Table
      rows={versions}
      rowKey={(v) => v.id}
      emptyMessage="Esta fuente aún no tiene versiones importadas."
      columns={[
        {
          key: "version_label",
          header: "Versión",
          render: (v) => (
            <Link href={`/tax-schedules/versions/${v.id}`} style={{ fontWeight: 600 }}>
              {v.version_label}
            </Link>
          ),
        },
        { key: "fiscal_year", header: "Año fiscal", render: (v) => v.fiscal_year },
        { key: "schedule_type", header: "Tipo", render: (v) => v.schedule_type },
        { key: "scope_category_code", header: "Categoría de alcance", render: (v) => v.scope_category_code ?? "General" },
        { key: "status", header: "Estado", render: (v) => STATUS_LABEL[v.status] ?? v.status },
      ]}
    />
  );
}
