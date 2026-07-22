import { Table } from "@/components/ui/Table";
import type { ApprovalHistoryEntry } from "@/lib/types/taxSchedule";

export function ApprovalHistoryList({ history }: { history: ApprovalHistoryEntry[] }) {
  return (
    <Table
      rows={history}
      rowKey={(h) => h.id}
      emptyMessage="Sin historial de aprobación todavía."
      columns={[
        { key: "created_at", header: "Fecha", render: (h) => new Date(h.created_at).toLocaleString() },
        { key: "transition", header: "Transición", render: (h) => `${h.from_status ?? "—"} → ${h.to_status}` },
        { key: "comment", header: "Comentario", render: (h) => h.comment ?? "—" },
      ]}
    />
  );
}
