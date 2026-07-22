"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Table } from "@/components/ui/Table";
import type { TaxCategoryCatalogItem } from "@/lib/types/taxCategory";

interface CategoryCatalogPanelProps {
  catalog: TaxCategoryCatalogItem[];
  onCreate: (code: string, name: string, description: string | null) => Promise<void>;
}

export function CategoryCatalogPanel({ catalog, onCreate }: CategoryCatalogPanelProps) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!code.trim() || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await onCreate(code.trim(), name.trim(), description.trim() || null);
      setCode("");
      setName("");
      setDescription("");
    } catch {
      setError("No se pudo crear la categoría. Verifica que el código no exista ya.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <h2 style={{ fontSize: "1.1rem" }}>Catálogo de categorías tributarias</h2>

      <Table
        rows={catalog}
        rowKey={(item) => item.id}
        emptyMessage="Aún no hay categorías en el catálogo."
        columns={[
          { key: "code", header: "Código", render: (item) => item.code },
          { key: "name", header: "Nombre", render: (item) => item.name },
          { key: "description", header: "Descripción", render: (item) => item.description ?? "—" },
          { key: "is_active", header: "Estado", render: (item) => (item.is_active ? "Activa" : "Inactiva") },
        ]}
      />

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
        <Input label="Código (ej. BUEN_CONTRIBUYENTE)" name="code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
        <Input label="Nombre" name="name" value={name} onChange={(e) => setName(e.target.value)} />
        <Input label="Descripción" name="description" value={description} onChange={(e) => setDescription(e.target.value)} />
        <Button type="button" disabled={!code.trim() || !name.trim() || submitting} onClick={handleSubmit}>
          {submitting ? "Creando…" : "Crear categoría"}
        </Button>
      </div>
      {error && <div className="error-banner">{error}</div>}
    </section>
  );
}
