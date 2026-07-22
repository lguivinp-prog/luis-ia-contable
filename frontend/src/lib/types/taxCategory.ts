export interface TaxCategoryCatalogItem {
  id: string;
  code: string;
  name: string;
  description: string | null;
  is_active: boolean;
}

export interface ClientTaxCategory {
  id: string;
  client_id: string;
  category_code: string;
  valid_from: string;
  valid_until: string | null;
  notes: string | null;
  created_at: string;
}
