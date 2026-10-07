import { describe, expect, it, vi } from "vitest";

import { fetchMonthlyCockpit } from "@/lib/cockpit";
import { COCKPIT_HREF, canViewCockpit } from "@/lib/cockpitNav";
import {
  COCKPIT_RISK_SEMAPHORE,
  MONTHLY_PHASE_SEMAPHORE,
  SIRE_STATUS_SEMAPHORE,
  buildCockpitQueryString,
  buildSummaryCards,
  declarationsLabel,
  defaultPeriodId,
  feeLabel,
  formatDate,
  formatPen,
  monthlyPeriodsNewestFirst,
  rowActions,
  taxLabel,
} from "@/lib/cockpitPresentation";
import type { User } from "@/lib/types/auth";
import type { CockpitRow, CockpitSummary } from "@/lib/types/cockpit";

vi.mock("@/lib/apiClient", () => ({ apiClient: { get: vi.fn().mockResolvedValue({ items: [] }) } }));

function row(overrides: Partial<CockpitRow> = {}): CockpitRow {
  return {
    client_id: "c1", business_name: "Cliente SAC", ruc: "20123456789", ruc_last_digit: 9, service_status: "activo",
    tax_period_id: "p1", tax_period_code: "2026-01", tax_period_display: "Enero 2026", monthly_phase: "OPERACION",
    period_generated: true, nearest_due_date: "2026-02-12", declaration_count: 4, filed_declaration_count: 1,
    pending_declaration_count: 3, filing_progress: "PARTIAL", document_status: "PARTIAL", missing_documents_count: 2,
    sire_status: "NOT_STARTED", sire_rvie_status: "PRESENTED", sire_rce_status: "NOT_STARTED",
    tax_total_assessed: "1000.00", tax_total_payable: "1000.00", tax_total_paid: "500.00", tax_balance: "500.00",
    tax_payment_status: "PARTIALLY_PAID", fee_total_amount: "400.00", fee_total_paid: "0.00", fee_balance: "400.00",
    fee_worst_collection_risk: "ON_TRACK", overall_risk: "PENDING_TAX", declaration_worst_risk: "ON_TRACK",
    ...overrides,
  };
}

describe("contrato de la API", () => {
  it("consulta SOLO al backend (/cockpit/monthly) con el periodo y filtros", async () => {
    const { apiClient } = await import("@/lib/apiClient");
    await fetchMonthlyCockpit({ tax_period_id: "p1", search: "alfa", sire_status: "NOT_CONFIGURED", limit: 25, offset: 50 });
    const url = vi.mocked(apiClient.get).mock.calls[0][0];
    expect(url.startsWith("/cockpit/monthly?")).toBe(true);
    expect(url).toContain("tax_period_id=p1");
    expect(url).toContain("search=alfa");
    expect(url).toContain("sire_status=NOT_CONFIGURED");
    expect(url).toContain("limit=25");
    expect(url).toContain("offset=50");
  });

  it("omite los filtros vacios", () => {
    const qs = buildCockpitQueryString({ tax_period_id: "p1", search: "  ", overall_risk: "", limit: 25 });
    expect(qs).toBe("tax_period_id=p1&limit=25");
  });
});

describe("presentacion de una fila por cliente", () => {
  it("cuenta presentadas sobre aplicables y NO multiplica filas", () => {
    expect(declarationsLabel(row())).toBe("1/4 presentadas");
    expect(declarationsLabel(row({ declaration_count: 0 }))).toBe("Sin obligaciones aplicables");
  });

  it("un cliente sin declaraciones muestra 'Periodo sin generar' y enlaza a la generacion existente", () => {
    const empty = row({ period_generated: false, declaration_count: 0, overall_risk: "PERIOD_NOT_GENERATED" });
    expect(declarationsLabel(empty)).toBe("Periodo sin generar");
    const actions = rowActions(empty);
    expect(actions.map((a) => a.href)).toEqual(["/clients/c1", "/declarations/generate"]);
    expect(COCKPIT_RISK_SEMAPHORE.PERIOD_NOT_GENERATED.label).toBe("Periodo sin generar");
  });

  it("solo ofrece acciones reales (sin botones de SUNAT)", () => {
    for (const r of [row(), row({ period_generated: false })]) {
      for (const action of rowActions(r)) {
        expect(action.href.startsWith("/clients/") || action.href === "/declarations/generate").toBe(true);
        expect(action.label).not.toMatch(/sunat|sol|buz|sire/i);
      }
    }
  });

  it("impuesto y honorarios sin datos no se inventan", () => {
    expect(taxLabel(row({ tax_total_payable: null, tax_total_assessed: null }))).toBeNull();
    expect(feeLabel(row({ fee_total_amount: "0.00", fee_balance: "0.00" }))).toBeNull();
    expect(taxLabel(row())).toBe("S/ 500.00 de S/ 1,000.00");
    expect(feeLabel(row())).toContain("S/ 400.00");
  });

  it("formatea fechas y montos", () => {
    expect(formatDate("2026-02-12")).toBe("12/02/2026");
    expect(formatDate(null)).toBe("—");
    expect(formatPen(null)).toBe("—");
    expect(formatPen("1234.5")).toBe("S/ 1,234.50");
  });
});

describe("estados SIRE, fase y riesgo", () => {
  it("SIRE: los 6 estados tienen etiqueta y NOT_CONFIGURED es distinto de NOT_APPLICABLE", () => {
    for (const status of ["NOT_CONFIGURED", "NOT_STARTED", "IN_PROGRESS", "READY_FOR_SUNAT", "PRESENTED", "NOT_APPLICABLE"]) {
      expect(SIRE_STATUS_SEMAPHORE[status].label.length).toBeGreaterThan(0);
    }
    expect(SIRE_STATUS_SEMAPHORE.NOT_CONFIGURED.label).not.toBe(SIRE_STATUS_SEMAPHORE.NOT_APPLICABLE.label);
  });

  it("fase: las 4 fases que devuelve el backend tienen presentacion", () => {
    for (const phase of ["PRE_APERTURA", "APERTURA", "OPERACION", "CIERRE"]) {
      expect(MONTHLY_PHASE_SEMAPHORE[phase]).toBeDefined();
    }
  });

  it("riesgo: cubre todos los valores del backend", () => {
    for (const risk of [
      "CRITICAL", "OVERDUE", "DUE_TODAY", "PENDING_TAX", "PENDING_DOCUMENTS", "PERIOD_NOT_GENERATED",
      "CLOSED_WITH_PENDING", "PENDING_FEES", "UPCOMING", "ON_TRACK", "CLOSED", "NOT_APPLICABLE",
    ]) {
      expect(COCKPIT_RISK_SEMAPHORE[risk]).toBeDefined();
    }
  });
});

describe("tarjetas resumen", () => {
  const summary: CockpitSummary = {
    active_clients: 20, period_not_generated: 2, due_soon: 3, overdue: 1, documents_incomplete: 5, sire_pending: 4,
    declarations_pending: 8, tax_payment_pending: 6, critical: 1,
  };

  it("muestra exactamente las 9 metricas soportadas, sin metricas no respaldadas", () => {
    const cards = buildSummaryCards(summary);
    expect(cards).toHaveLength(9);
    expect(cards.map((c) => c.value)).toEqual([20, 2, 3, 1, 5, 4, 8, 6, 1]);
    for (const card of cards) expect(card.label).not.toMatch(/buz|contabilidad|comunicaci/i);
  });
});

describe("selector de periodo", () => {
  const periods = [
    { id: "a", period_type: "MONTHLY", start_date: "2026-08-01", end_date: "2026-08-31" },
    { id: "b", period_type: "MONTHLY", start_date: "2026-09-01", end_date: "2026-09-30" },
    { id: "c", period_type: "MONTHLY", start_date: "2026-10-01", end_date: "2026-10-31" },
    { id: "x", period_type: "ANNUAL", start_date: "2026-01-01", end_date: "2026-12-31" },
  ];

  it("solo lista periodos mensuales, del mas reciente al mas antiguo", () => {
    expect(monthlyPeriodsNewestFirst(periods).map((p) => p.id)).toEqual(["c", "b", "a"]);
  });

  it("por defecto elige el ultimo periodo ya terminado (el trabajo ocurre en el mes siguiente)", () => {
    expect(defaultPeriodId(periods, new Date(2026, 9, 7))).toBe("b"); // 7-oct: septiembre
    expect(defaultPeriodId(periods, new Date(2026, 10, 2))).toBe("c"); // 2-nov: octubre
    expect(defaultPeriodId([], new Date())).toBeNull();
    expect(defaultPeriodId(periods.slice(2, 3), new Date(2026, 9, 7))).toBe("c"); // sin terminados: el mas reciente
  });
});

describe("navegacion y permisos", () => {
  const user = (permissions: string[]) => ({ permissions }) as unknown as User;

  it("el cockpit se muestra con el permiso de lectura del tablero y no requiere permisos nuevos", () => {
    expect(COCKPIT_HREF).toBe("/cockpit");
    expect(canViewCockpit(user(["dashboard:read"]))).toBe(true);
    expect(canViewCockpit(user(["client:read"]))).toBe(false);
    expect(canViewCockpit(null)).toBe(false);
  });
});
