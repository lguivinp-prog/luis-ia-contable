import { Button } from "@/components/ui/Button";

interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
}

export function Pagination({ total, limit, offset, onOffsetChange }: PaginationProps) {
  if (total <= limit) {
    return null;
  }

  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginTop: "1rem" }}>
      <Button
        type="button"
        variant="secondary"
        disabled={offset === 0}
        onClick={() => onOffsetChange(Math.max(0, offset - limit))}
      >
        Anterior
      </Button>
      <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
        Página {currentPage} de {totalPages} ({total} en total)
      </span>
      <Button
        type="button"
        variant="secondary"
        disabled={offset + limit >= total}
        onClick={() => onOffsetChange(offset + limit)}
      >
        Siguiente
      </Button>
    </div>
  );
}
