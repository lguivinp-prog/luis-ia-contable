"use client";

import { useCallback, useEffect, useState } from "react";

import { CategoryCatalogPanel } from "@/components/taxCategories/CategoryCatalogPanel";
import { ClientCategoryPanel } from "@/components/taxCategories/ClientCategoryPanel";
import { apiClient } from "@/lib/apiClient";
import type { TaxCategoryCatalogItem } from "@/lib/types/taxCategory";

export default function TaxCategoriesPage() {
  const [catalog, setCatalog] = useState<TaxCategoryCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const data = await apiClient.get<TaxCategoryCatalogItem[]>("/tax-categories/catalog");
    setCatalog(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => reload());
  }, [reload]);

  async function handleCreate(code: string, name: string, description: string | null) {
    await apiClient.post("/tax-categories/catalog", { code, name, description });
    await reload();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Categorías tributarias</h1>

      {loading ? (
        <p>Cargando…</p>
      ) : (
        <>
          <CategoryCatalogPanel catalog={catalog} onCreate={handleCreate} />
          <ClientCategoryPanel catalog={catalog} />
        </>
      )}
    </div>
  );
}
