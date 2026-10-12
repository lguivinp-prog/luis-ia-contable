"use client";

import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import type { ServiceStatus, TaxRegime } from "@/lib/types/client";

export interface ClientFilters {
  search: string;
  serviceStatus: ServiceStatus | "";
  taxRegime: TaxRegime | "";
}

const SERVICE_STATUS_OPTIONS = [
  { value: "prospecto", label: "Prospecto" },
  { value: "activo", label: "Activo" },
  { value: "suspendido", label: "Suspendido" },
  { value: "pendiente_cierre", label: "Pendiente de cierre" },
  { value: "inactivo", label: "Inactivo" },
];

const TAX_REGIME_OPTIONS = [
  { value: "NRUS", label: "NRUS" },
  { value: "RER", label: "RER" },
  { value: "RMT", label: "RMT" },
  { value: "SIN_DETERMINAR", label: "Sin determinar" },
  { value: "GENERAL", label: "General" },
];

interface ClientFilterBarProps {
  filters: ClientFilters;
  onChange: (filters: ClientFilters) => void;
}

export function ClientFilterBar({ filters, onChange }: ClientFilterBarProps) {
  return (
    <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
      <Input
        label="Buscar por RUC o razón social"
        name="search"
        value={filters.search}
        onChange={(event) => onChange({ ...filters, search: event.target.value })}
      />
      <Select
        label="Estado de servicio"
        name="serviceStatus"
        placeholder="Todos"
        options={SERVICE_STATUS_OPTIONS}
        value={filters.serviceStatus}
        onChange={(event) =>
          onChange({ ...filters, serviceStatus: event.target.value as ClientFilters["serviceStatus"] })
        }
      />
      <Select
        label="Régimen tributario"
        name="taxRegime"
        placeholder="Todos"
        options={TAX_REGIME_OPTIONS}
        value={filters.taxRegime}
        onChange={(event) => onChange({ ...filters, taxRegime: event.target.value as ClientFilters["taxRegime"] })}
      />
    </div>
  );
}
