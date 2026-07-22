"use client";

import { useRouter } from "next/navigation";

import { ClientForm } from "@/components/clients/ClientForm";
import { apiClient } from "@/lib/apiClient";
import type { Client, ClientBase } from "@/lib/types/client";

export default function NewClientPage() {
  const router = useRouter();

  async function handleSubmit(values: ClientBase) {
    const client = await apiClient.post<Client>("/clients", values);
    router.push(`/clients/${client.id}`);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Nuevo contribuyente</h1>
      <ClientForm onSubmit={handleSubmit} submitLabel="Crear contribuyente" />
    </div>
  );
}
