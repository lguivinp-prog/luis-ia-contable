export interface ObligationCatalogItem {
  id: string;
  code: string;
  name: string;
  description: string | null;
  periodicity: string;
  applies_to_regime: string[] | null;
  source: string;
  source_version: string;
  is_active: boolean;
}

export interface ClientObligation {
  id: string;
  client_id: string;
  obligation_id: string;
  is_active: boolean;
  assigned_at: string;
  deactivated_at: string | null;
  notes: string | null;
  obligation: ObligationCatalogItem | null;
}
