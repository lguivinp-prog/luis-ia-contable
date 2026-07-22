import { Table } from "@/components/ui/Table";
import type { TaxScheduleEntry } from "@/lib/types/taxSchedule";

export function EntriesTable({ entries }: { entries: TaxScheduleEntry[] }) {
  return (
    <Table
      rows={entries}
      rowKey={(e) => e.id}
      emptyMessage="Esta versión no tiene entradas."
      columns={[
        { key: "tax_period", header: "Periodo", render: (e) => e.tax_period },
        {
          key: "applicable_last_digits",
          header: "Últimos dígitos",
          render: (e) => (e.applicable_last_digits ? e.applicable_last_digits.join(", ") : "Todos"),
        },
        { key: "required_category_code", header: "Categoría requerida", render: (e) => e.required_category_code ?? "—" },
        { key: "due_date_effective", header: "Vencimiento vigente", render: (e) => e.due_date_effective },
        { key: "due_date_original", header: "Vencimiento original", render: (e) => e.due_date_original },
        { key: "due_date_type", header: "Tipo", render: (e) => e.due_date_type },
      ]}
    />
  );
}
