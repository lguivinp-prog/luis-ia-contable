"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import type { ObligationCatalogItem } from "@/lib/types/obligation";
import type { TaxScheduleSource } from "@/lib/types/taxSchedule";

interface SourcesPanelProps {
  sources: TaxScheduleSource[];
  obligations: ObligationCatalogItem[];
  onCreate: (obligationId: string, name: string, description: string | null, referenceUrl: string | null) => Promise<void>;
}

export function SourcesPanel({ sources, obligations, onCreate }: SourcesPanelProps) {
  const [obligationId, setObligationId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [referenceUrl, setReferenceUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const obligationsById = new Map(obligations.map((o) => [o.id, o]));

  async function handleSubmit() {
    if (!obligationId || !name.trim()) return;
    setSubmitting(true);
    try {
      await onCreate(obligationId, name.trim(), description.trim() || null, referenceUrl.trim() || null);
      setObligationId("");
      setName("");
      setDescription("");
      setReferenceUrl("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <Table
        rows={sources}
        rowKey={(s) => s.id}
        emptyMessage="Aún no hay fuentes de cronograma registradas."
        columns={[
          {
            key: "name",
            header: "Nombre",
            render: (s) => (
              <Link href={`/tax-schedules/${s.id}`} style={{ fontWeight: 600 }}>
                {s.name}
              </Link>
            ),
          },
          {
            key: "obligation",
            header: "Obligación",
            render: (s) => obligationsById.get(s.obligation_id)?.name ?? s.obligation_id,
          },
          { key: "description", header: "Descripción", render: (s) => s.description ?? "—" },
          { key: "is_active", header: "Estado", render: (s) => (s.is_active ? "Activa" : "Inactiva") },
        ]}
      />

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
        <Select
          label="Obligación asociada"
          name="obligation_id"
          placeholder="Selecciona una obligación"
          options={obligations.map((o) => ({ value: o.id, label: `${o.code} — ${o.name}` }))}
          value={obligationId}
          onChange={(e) => setObligationId(e.target.value)}
        />
        <Input label="Nombre de la fuente" name="name" value={name} onChange={(e) => setName(e.target.value)} />
        <Input label="Descripción" name="description" value={description} onChange={(e) => setDescription(e.target.value)} />
        <Input
          label="URL de referencia"
          name="reference_url"
          value={referenceUrl}
          onChange={(e) => setReferenceUrl(e.target.value)}
        />
        <Button type="button" disabled={!obligationId || !name.trim() || submitting} onClick={handleSubmit}>
          {submitting ? "Creando…" : "Crear fuente"}
        </Button>
      </div>
    </section>
  );
}
