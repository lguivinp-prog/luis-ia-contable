"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import { apiClient, ApiError } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import type { ClientObligation, ObligationCatalogItem } from "@/lib/types/obligation";
import type {
  ClientDocumentRequirement,
  DocumentRequirementCatalog,
} from "@/lib/types/declarationDocument";

interface CatalogFormState {
  code: string;
  name: string;
  description: string;
  category: string;
}

interface RequirementFormState {
  requirementCatalogId: string;
  clientObligationId: string;
  periodicity: string;
  validFrom: string;
  validUntil: string;
  description: string;
  reminderLeadDays: string;
}

function emptyCatalogForm(): CatalogFormState {
  return { code: "", name: "", description: "", category: "" };
}

function emptyRequirementForm(): RequirementFormState {
  return {
    requirementCatalogId: "",
    clientObligationId: "",
    periodicity: "MONTHLY",
    validFrom: new Date().toISOString().slice(0, 10),
    validUntil: "",
    description: "",
    reminderLeadDays: "5",
  };
}

export default function ClientDocumentsPage() {
  const params = useParams<{ id: string }>();
  const clientId = params.id;
  const { user } = useCurrentUser();
  const canManage = hasPermission(user, "document:manage");

  const [catalog, setCatalog] = useState<DocumentRequirementCatalog[]>([]);
  const [requirements, setRequirements] = useState<ClientDocumentRequirement[]>([]);
  const [obligations, setObligations] = useState<ClientObligation[]>([]);
  const [obligationCatalog, setObligationCatalog] = useState<ObligationCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [catalogForm, setCatalogForm] = useState<CatalogFormState>(emptyCatalogForm);
  const [creatingCatalog, setCreatingCatalog] = useState(false);

  const [requirementForm, setRequirementForm] = useState<RequirementFormState>(emptyRequirementForm);
  const [assigning, setAssigning] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [catalogData, requirementsData, obligationsData, obligationCatalogData] = await Promise.all([
        apiClient.get<DocumentRequirementCatalog[]>("/document-requirement-catalog"),
        apiClient.get<ClientDocumentRequirement[]>(`/clients/${clientId}/document-requirements`),
        apiClient.get<ClientObligation[]>(`/clients/${clientId}/obligations`),
        apiClient.get<ObligationCatalogItem[]>("/obligations/catalog"),
      ]);
      setCatalog(catalogData);
      setRequirements(requirementsData);
      setObligations(obligationsData);
      setObligationCatalog(obligationCatalogData);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo cargar los requisitos.");
    } finally {
      setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleCreateCatalogEntry() {
    if (!catalogForm.code.trim() || !catalogForm.name.trim()) return;
    setCreatingCatalog(true);
    setError(null);
    try {
      await apiClient.post("/document-requirement-catalog", {
        code: catalogForm.code,
        name: catalogForm.name,
        description: catalogForm.description || null,
        category: catalogForm.category || null,
      });
      setCatalogForm(emptyCatalogForm());
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo crear el requisito de catálogo.");
    } finally {
      setCreatingCatalog(false);
    }
  }

  async function handleAssignRequirement() {
    if (!requirementForm.requirementCatalogId || !requirementForm.validFrom) return;
    setAssigning(true);
    setError(null);
    try {
      await apiClient.post(`/clients/${clientId}/document-requirements`, {
        requirement_catalog_id: requirementForm.requirementCatalogId,
        client_obligation_id: requirementForm.clientObligationId || null,
        is_mandatory: true,
        periodicity: requirementForm.periodicity,
        valid_from: requirementForm.validFrom,
        valid_until: requirementForm.validUntil || null,
        description: requirementForm.description || null,
        responsible_user_id: null,
        reminder_lead_days: Number(requirementForm.reminderLeadDays) || 0,
      });
      setRequirementForm(emptyRequirementForm());
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.detail) : "No se pudo asignar el requisito.");
    } finally {
      setAssigning(false);
    }
  }

  const catalogById = new Map(catalog.map((c) => [c.id, c]));
  const obligationCatalogById = new Map(obligationCatalog.map((o) => [o.id, o]));
  const obligationById = new Map(
    obligations.map((o) => [
      o.id,
      { ...o, obligation: o.obligation ?? obligationCatalogById.get(o.obligation_id) ?? null },
    ]),
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "780px" }}>
      <div>
        <Link href={`/clients/${clientId}`} style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          ← Volver al contribuyente
        </Link>
        <h1 style={{ fontSize: "1.4rem", marginTop: "0.4rem" }}>Requisitos documentales</h1>
      </div>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      {loading ? (
        <p>Cargando…</p>
      ) : (
        <>
          {canManage && (
          <>
          <section className="surface-card" style={{ padding: "1.25rem" }}>
            <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Nuevo requisito de catálogo</h2>
            <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "flex-end" }}>
              <Input
                label="Código"
                value={catalogForm.code}
                onChange={(e) => setCatalogForm((prev) => ({ ...prev, code: e.target.value }))}
              />
              <Input
                label="Nombre"
                value={catalogForm.name}
                onChange={(e) => setCatalogForm((prev) => ({ ...prev, name: e.target.value }))}
              />
              <Input
                label="Categoría"
                value={catalogForm.category}
                onChange={(e) => setCatalogForm((prev) => ({ ...prev, category: e.target.value }))}
              />
              <Button
                type="button"
                variant="secondary"
                onClick={handleCreateCatalogEntry}
                disabled={creatingCatalog || !catalogForm.code.trim() || !catalogForm.name.trim()}
              >
                {creatingCatalog ? "Creando…" : "Crear en catálogo"}
              </Button>
            </div>
          </section>

          <section className="surface-card" style={{ padding: "1.25rem" }}>
            <h2 style={{ fontSize: "1.05rem", marginBottom: "0.75rem" }}>Asignar requisito al cliente</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", maxWidth: "420px" }}>
              <Select
                label="Requisito de catálogo"
                placeholder="Selecciona…"
                options={catalog.map((c) => ({
                  value: c.id,
                  label: `${c.code} — ${c.name}${c.is_system ? " (sistema)" : ""}`,
                }))}
                value={requirementForm.requirementCatalogId}
                onChange={(e) =>
                  setRequirementForm((prev) => ({ ...prev, requirementCatalogId: e.target.value }))
                }
              />
              <Select
                label="Vincular a una obligación (opcional)"
                placeholder="General del cliente (no vinculado)"
                options={obligations.map((o) => {
                  const enriched = obligationById.get(o.id);
                  return {
                    value: o.id,
                    label: enriched?.obligation
                      ? `${enriched.obligation.code} — ${enriched.obligation.name}`
                      : o.obligation_id,
                  };
                })}
                value={requirementForm.clientObligationId}
                onChange={(e) =>
                  setRequirementForm((prev) => ({ ...prev, clientObligationId: e.target.value }))
                }
              />
              <Select
                label="Periodicidad"
                options={[
                  { value: "MONTHLY", label: "Mensual" },
                  { value: "ANNUAL", label: "Anual" },
                  { value: "ONCE", label: "Una vez" },
                ]}
                value={requirementForm.periodicity}
                onChange={(e) => setRequirementForm((prev) => ({ ...prev, periodicity: e.target.value }))}
              />
              <Input
                label="Vigente desde"
                type="date"
                value={requirementForm.validFrom}
                onChange={(e) => setRequirementForm((prev) => ({ ...prev, validFrom: e.target.value }))}
              />
              <Input
                label="Vigente hasta (opcional)"
                type="date"
                value={requirementForm.validUntil}
                onChange={(e) => setRequirementForm((prev) => ({ ...prev, validUntil: e.target.value }))}
              />
              <Input
                label="Días de aviso previo"
                type="number"
                min="0"
                value={requirementForm.reminderLeadDays}
                onChange={(e) =>
                  setRequirementForm((prev) => ({ ...prev, reminderLeadDays: e.target.value }))
                }
              />
              <Button
                type="button"
                onClick={handleAssignRequirement}
                disabled={assigning || !requirementForm.requirementCatalogId}
              >
                {assigning ? "Asignando…" : "Asignar requisito"}
              </Button>
            </div>
          </section>
          </>
          )}

          <Table
            rows={requirements}
            rowKey={(r) => r.id}
            emptyMessage="Este cliente no tiene requisitos documentales asignados todavía."
            columns={[
              {
                key: "name",
                header: "Requisito",
                render: (r) => catalogById.get(r.requirement_catalog_id)?.name ?? r.requirement_catalog_id,
              },
              {
                key: "scope",
                header: "Vínculo",
                render: (r) =>
                  r.client_obligation_id
                    ? obligationById.get(r.client_obligation_id)?.obligation?.name ?? "Obligación específica"
                    : "General del cliente",
              },
              { key: "periodicity", header: "Periodicidad", render: (r) => r.periodicity },
              {
                key: "validity",
                header: "Vigencia",
                render: (r) => `${r.valid_from} → ${r.valid_until ?? "indefinido"}`,
              },
              {
                key: "status",
                header: "Estado",
                render: (r) => (
                  <span className={`status-pill ${r.is_active ? "status-verde" : "status-gris"}`}>
                    {r.is_active ? "Activo" : "Inactivo"}
                  </span>
                ),
              },
            ]}
          />
        </>
      )}
    </div>
  );
}
