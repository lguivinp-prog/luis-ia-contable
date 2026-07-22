"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import { apiClient } from "@/lib/apiClient";
import type { Client, ClientListResponse } from "@/lib/types/client";
import type { ClientTaxCategory, TaxCategoryCatalogItem } from "@/lib/types/taxCategory";

interface ClientCategoryPanelProps {
  catalog: TaxCategoryCatalogItem[];
}

export function ClientCategoryPanel({ catalog }: ClientCategoryPanelProps) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Client[]>([]);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [assignments, setAssignments] = useState<ClientTaxCategory[]>([]);
  const [categoryCode, setCategoryCode] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const timeout = setTimeout(() => {
      if (!search.trim()) {
        setResults([]);
        return;
      }
      const params = new URLSearchParams({ search: search.trim(), limit: "10" });
      apiClient.get<ClientListResponse>(`/clients?${params.toString()}`).then((data) => setResults(data.items));
    }, 250);
    return () => clearTimeout(timeout);
  }, [search]);

  async function loadAssignments(clientId: string) {
    const data = await apiClient.get<ClientTaxCategory[]>(`/clients/${clientId}/tax-categories`);
    setAssignments(data);
  }

  async function selectClient(client: Client) {
    setSelectedClient(client);
    setResults([]);
    setSearch(client.business_name);
    setError(null);
    await loadAssignments(client.id);
  }

  async function handleAssign() {
    if (!selectedClient || !categoryCode || !validFrom) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiClient.post(`/clients/${selectedClient.id}/tax-categories`, {
        category_code: categoryCode,
        valid_from: validFrom,
        valid_until: validUntil || null,
        notes: notes.trim() || null,
      });
      setCategoryCode("");
      setValidFrom("");
      setValidUntil("");
      setNotes("");
      await loadAssignments(selectedClient.id);
    } catch {
      setError("No se pudo asignar la categoría (revisa que la vigencia no se solape con otra existente).");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove(assignmentId: string) {
    if (!selectedClient) return;
    await apiClient.delete(`/clients/${selectedClient.id}/tax-categories/${assignmentId}`);
    await loadAssignments(selectedClient.id);
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      <h2 style={{ fontSize: "1.1rem" }}>Categorías por contribuyente</h2>

      <div style={{ position: "relative", maxWidth: "420px" }}>
        <Input
          label="Buscar contribuyente (RUC o razón social)"
          name="client-search"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setSelectedClient(null);
          }}
        />
        {results.length > 0 && (
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: "0.25rem",
              position: "absolute",
              zIndex: 1,
              width: "100%",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "6px",
            }}
          >
            {results.map((client) => (
              <li key={client.id}>
                <button
                  type="button"
                  onClick={() => selectClient(client)}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    padding: "0.4rem 0.5rem",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  {client.ruc} — {client.business_name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {selectedClient && (
        <>
          <Table
            rows={assignments}
            rowKey={(a) => a.id}
            emptyMessage="Este contribuyente no tiene categorías asignadas."
            columns={[
              { key: "category_code", header: "Categoría", render: (a) => a.category_code },
              { key: "valid_from", header: "Vigente desde", render: (a) => a.valid_from },
              { key: "valid_until", header: "Vigente hasta", render: (a) => a.valid_until ?? "Indefinido" },
              { key: "notes", header: "Notas", render: (a) => a.notes ?? "—" },
              {
                key: "actions",
                header: "",
                render: (a) => (
                  <Button type="button" variant="danger" onClick={() => handleRemove(a.id)}>
                    Quitar
                  </Button>
                ),
              },
            ]}
          />

          <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
            <Select
              label="Categoría"
              name="category_code"
              placeholder="Selecciona una categoría"
              options={catalog.map((c) => ({ value: c.code, label: `${c.code} — ${c.name}` }))}
              value={categoryCode}
              onChange={(e) => setCategoryCode(e.target.value)}
            />
            <Input
              label="Vigente desde"
              name="valid_from"
              type="date"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
            <Input
              label="Vigente hasta (opcional)"
              name="valid_until"
              type="date"
              value={validUntil}
              onChange={(e) => setValidUntil(e.target.value)}
            />
            <Input label="Notas" name="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <Button
              type="button"
              disabled={!categoryCode || !validFrom || submitting}
              onClick={handleAssign}
            >
              {submitting ? "Asignando…" : "Asignar"}
            </Button>
          </div>
          {error && <div className="error-banner">{error}</div>}
        </>
      )}
    </section>
  );
}
