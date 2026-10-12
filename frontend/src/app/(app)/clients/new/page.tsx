"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { AssistedOnboarding } from "@/components/clients/AssistedOnboarding";
import { ClientForm } from "@/components/clients/ClientForm";
import { Button } from "@/components/ui/Button";
import { apiClient } from "@/lib/apiClient";
import { useCurrentUser } from "@/lib/currentUserContext";
import { canOnboardAssisted } from "@/lib/sunatPermissions";
import type { Client, ClientBase } from "@/lib/types/client";

export default function NewClientPage() {
  return (
    <Suspense fallback={<p>Cargando…</p>}>
      <NewClient />
    </Suspense>
  );
}

function NewClient() {
  const router = useRouter();
  const search = useSearchParams();
  const { user } = useCurrentUser();
  const assistedAllowed = canOnboardAssisted(user);
  // Un alta asistida en curso (id en la URL) abre directamente ese modo para poder retomarla tras refrescar.
  const [mode, setMode] = useState<"manual" | "assisted">(search.get("onboarding") ? "assisted" : "manual");

  async function handleSubmit(values: ClientBase) {
    const client = await apiClient.post<Client>("/clients", values);
    router.push(`/clients/${client.id}`);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Nuevo contribuyente</h1>

      <div role="group" aria-label="Tipo de alta" style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <Button type="button" variant={mode === "manual" ? "primary" : "secondary"} aria-pressed={mode === "manual"} onClick={() => setMode("manual")}>
          Alta manual
        </Button>
        {assistedAllowed && (
          <Button type="button" variant={mode === "assisted" ? "primary" : "secondary"} aria-pressed={mode === "assisted"} onClick={() => setMode("assisted")}>
            Alta asistida por SUNAT
          </Button>
        )}
      </div>

      {mode === "manual" || !assistedAllowed ? (
        <ClientForm onSubmit={handleSubmit} submitLabel="Crear contribuyente" />
      ) : (
        <AssistedOnboarding />
      )}
    </div>
  );
}
