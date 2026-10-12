import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  UNCHANGED_MESSAGE,
  describeInternalRegime,
  describeOfficialRegime,
  formatDiffValue,
  verificationOutcome,
} from "@/lib/fichaRucModel";
import type { FichaOverview } from "@/lib/types/fichaRuc";

function overview(partial: Partial<FichaOverview> = {}): FichaOverview {
  return {
    client_id: "c1",
    official_regime: { value: null, label: null, source: null, as_of: null, snapshot_id: null },
    internal_tax_regime: "GENERAL",
    internal_regime_source: "NO_REGISTRADO",
    internal_regime_changed_at: null,
    latest_accepted: null,
    pending_snapshot_id: null,
    last_verified_at: null,
    last_unchanged_verification_at: null,
    sensitive_sections_visible: true,
    ...partial,
  };
}

const RMT = { value: "RMT", label: "x", source: "SUNAT_FICHA_RUC", as_of: "2026-10-09T17:00:00Z", snapshot_id: "s1" };

describe("régimen oficial vs. valor por defecto", () => {
  it("sin snapshot aceptado NO se muestra ningún régimen como oficial (nunca el interno por defecto)", () => {
    const view = describeOfficialRegime(overview());
    expect(view.confirmed).toBe(false);
    expect(view.text).toContain("no confirmado por SUNAT");
    expect(view.text).not.toContain("General");
  });

  it("con tributos afectos muestra el régimen oficial con su fecha", () => {
    const view = describeOfficialRegime(overview({ official_regime: RMT }));
    expect(view.confirmed).toBe(true);
    expect(view.text).toBe("Régimen MYPE Tributario (RMT)");
    expect(view.asOf).toBe("2026-10-09T17:00:00Z");
  });

  it("el valor interno se rotula como interno y avisa cuando difiere del oficial", () => {
    expect(describeInternalRegime(overview({ official_regime: RMT, internal_tax_regime: "GENERAL" }))).toContain("difiere del oficial");
    expect(describeInternalRegime(overview({ official_regime: RMT, internal_tax_regime: "RMT" }))).toContain("coincide con SUNAT");
    expect(describeInternalRegime(overview({ internal_tax_regime: "SIN_DETERMINAR" }))).toContain("sin confirmar con SUNAT");
  });
});

describe("Flujo B: resultado de la verificación", () => {
  it("«sin cambios» solo si la verificación ocurrió después de iniciar esa sesión y no hay propuesta", () => {
    const base = overview({ last_unchanged_verification_at: "2026-10-09T18:00:00Z" });
    expect(verificationOutcome(base, "2026-10-09T17:59:00Z")).toBe("UNCHANGED");
    expect(verificationOutcome(base, "2026-10-09T18:30:00Z")).toBe("NONE"); // verificación de una sesión anterior
    expect(verificationOutcome(overview(), "2026-10-09T18:30:00Z")).toBe("NONE"); // jamás se fabrica
    expect(verificationOutcome(overview({ ...base, pending_snapshot_id: "p1" }), "2026-10-09T17:59:00Z")).toBe("PROPOSAL");
    expect(verificationOutcome(null, null)).toBe("NONE");
    expect(UNCHANGED_MESSAGE).toBe("Ficha RUC verificada, sin cambios");
  });
});

describe("presentación de campos nuevos en la revisión", () => {
  it("régimen, tributos, establecimientos y representantes se muestran legibles", () => {
    expect(formatDiffValue("tax_regime", "RMT")).toBe("Régimen MYPE Tributario (RMT)");
    expect(formatDiffValue("tax_regime", "SIN_DETERMINAR")).toBe("Sin determinar");
    expect(formatDiffValue("tributos_afectos", [{ tributo: "SENCICO", afecto_desde: "2019-05-01" }, { tributo: "IGV" }])).toBe(
      "SENCICO (desde 01/05/2019); IGV",
    );
    expect(formatDiffValue("establecimientos_anexos", [{ codigo: "0001", tipo: "SUCURSAL", denominacion: "ANEXO" }])).toBe("0001 · SUCURSAL · ANEXO");
    expect(formatDiffValue("representantes_legales", [{ nombre: "PERSONA", cargo: "GERENTE" }])).toBe("PERSONA · GERENTE");
    expect(formatDiffValue("tributos_afectos", [])).toBe("—");
  });
});

describe("la UI no inventa un régimen por defecto", () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf-8");

  it("el alta manual ya no propone RMT por defecto y ofrece «Sin determinar»", () => {
    const form = read("src/components/clients/ClientForm.tsx");
    expect(form).toContain('tax_regime: "SIN_DETERMINAR"');
    expect(form).toContain("Sin determinar");
    expect(form).not.toContain('tax_regime: "RMT"');
  });

  it("Información SUNAT toma el régimen del snapshot y separa lo interno; el Centro SUNAT distingue las tres acciones", () => {
    const info = read("src/components/clients/SunatInformation.tsx");
    expect(info).toContain("describeOfficialRegime(overview)");
    expect(info).toContain("Dato interno del cliente");
    expect(info).toContain("sensitive_sections_visible");
    const center = read("src/app/(app)/clients/[id]/sol-access/page.tsx");
    expect(center).toContain("Abrir en SUNAT (solo ver)");
    expect(center).toContain("Actualizar datos desde Ficha RUC");
    expect(center).toContain("Abrir {DESTINATION_LABEL[destination]}");
    expect(center).toContain("UNCHANGED_MESSAGE");
  });
});

describe("procedencia del régimen interno", () => {
  const base = { official_regime: { value: null, label: null, source: null, as_of: null, snapshot_id: null }, internal_tax_regime: "RMT" } as const;

  it("una corrección manual se rotula como manual y NO como dato extraído de SUNAT", () => {
    const text = describeInternalRegime({ ...base, internal_regime_source: "MANUAL", internal_regime_changed_at: "2026-10-12T15:00:00Z" });
    expect(text).toContain("corregido manualmente");
    expect(text).toContain("no extraído de SUNAT");
  });

  it("si lo fijó una propuesta de la Ficha aceptada lo dice; sin registro previo es solo un dato interno", () => {
    expect(describeInternalRegime({ ...base, internal_regime_source: "SUNAT_FICHA_RUC", internal_regime_changed_at: null })).toContain("aceptado desde la Ficha RUC");
    expect(describeInternalRegime({ ...base, internal_regime_source: "NO_REGISTRADO", internal_regime_changed_at: null })).toContain("sin confirmar con SUNAT");
  });
});

describe("texto del Centro SUNAT", () => {
  it("usa singular y plural correctos para los cambios de la propuesta", () => {
    const center = readFileSync(join(process.cwd(), "src/app/(app)/clients/[id]/sol-access/page.tsx"), "utf-8");
    expect(center).toContain('=== 1 ? "cambio" : "cambios"');
    expect(center).not.toContain("length} cambios)");
  });
});

describe("guardado del cliente: confirmación visible y sin doble envío", () => {
  const form = readFileSync(join(process.cwd(), "src/components/clients/ClientForm.tsx"), "utf-8");
  it("muestra una confirmación al guardar y bloquea el reenvío mientras hay una solicitud en curso", () => {
    expect(form).toContain('data-testid="client-saved"');
    expect(form).toContain("setSaved(true)");
    expect(form).toContain("if (submitting) return");
    expect(form).toContain('role="alert"');
  });
  it("la ficha del cliente relee «Información SUNAT» tras guardar", () => {
    const page = readFileSync(join(process.cwd(), "src/app/(app)/clients/[id]/page.tsx"), "utf-8");
    expect(page).toContain("setInfoVersion");
    expect(page).toContain("key={infoVersion}");
  });
});
