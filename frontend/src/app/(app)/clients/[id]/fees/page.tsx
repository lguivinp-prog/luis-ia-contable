"use client";

import { useParams } from "next/navigation";

import { ClientFeesPanel } from "@/components/fees/ClientFeesPanel";

export default function ClientFeesPage() {
  const params = useParams<{ id: string }>();
  return <ClientFeesPanel clientId={params.id} />;
}
