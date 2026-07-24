"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { ClientFilterBar, type ClientFilters } from "@/components/clients/ClientFilterBar";
import { ClientsTable } from "@/components/clients/ClientsTable";
import { apiClient } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";
import { hasPermission } from "@/lib/permissions";
import type { ClientListResponse } from "@/lib/types/client";

const EMPTY_FILTERS: ClientFilters = { search: "", serviceStatus: "", taxRegime: "" };

export default function ClientsPage() {
  const { user } = useCurrentUser();
  const [filters, setFilters] = useState<ClientFilters>(EMPTY_FILTERS);
  const [data, setData] = useState<ClientListResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams();
    if (filters.search) params.set("search", filters.search);
    if (filters.serviceStatus) params.set("service_status", filters.serviceStatus);
    if (filters.taxRegime) params.set("tax_regime", filters.taxRegime);

    const timeout = setTimeout(() => {
      setLoading(true);
      apiClient
        .get<ClientListResponse>(`/clients?${params.toString()}`)
        .then(setData)
        .finally(() => setLoading(false));
    }, 250);

    return () => clearTimeout(timeout);
  }, [filters]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: "1.4rem" }}>Contribuyentes</h1>
        {hasPermission(user, "client:write") && (
          <Link href="/clients/new">
            <Button type="button">Nuevo contribuyente</Button>
          </Link>
        )}
      </div>

      <ClientFilterBar filters={filters} onChange={setFilters} />

      {loading ? <p>Cargando…</p> : <ClientsTable clients={data?.items ?? []} />}

      {data && (
        <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          {data.total} contribuyente{data.total === 1 ? "" : "s"} en total.
        </p>
      )}
    </div>
  );
}
