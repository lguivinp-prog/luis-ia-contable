// Barrido estatico de la superficie SUNAT del producto M2 (entorno vitest = Node, sin render de componentes): ningun
// secreto en almacenamiento/URL, destinos solo del enum cerrado, sin acciones SUNAT falsas, revision humana antes de escribir.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf-8");
const sources: Record<string, string> = {
  onboarding: read("src/components/clients/AssistedOnboarding.tsx"),
  review: read("src/components/clients/FichaReviewPanel.tsx"),
  reviewPage: read("src/app/(app)/clients/[id]/ficha-ruc/[snapshotId]/page.tsx"),
  solAccessPage: read("src/app/(app)/clients/[id]/sol-access/page.tsx"),
  newClientPage: read("src/app/(app)/clients/new/page.tsx"),
  api: read("src/lib/fichaRuc.ts"),
  solApi: read("src/lib/solAccess.ts"),
  model: read("src/lib/fichaRucModel.ts"),
};

describe("higiene de secretos", () => {
  it("ninguna pieza del alta/Ficha usa almacenamiento del navegador ni cookies", () => {
    for (const [name, source] of Object.entries(sources)) {
      expect(source, name).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
    }
  });

  it("usuario/clave SOL y clave de boveda son type=password o estado transitorio y se borran tras la solicitud", () => {
    const s = sources.onboarding;
    expect(s).toMatch(/<DeliberateInput secret id="onb-sol-password"/); // enmascarado, sin autocompletado
    expect(s).toMatch(/<DeliberateInput secret id="onb-vault-password"/);
    for (const clear of ['setSolUser("")', 'setSolPassword("")', 'setVaultPassword("")']) expect(s).toContain(clear);
  });

  it("los secretos solo viajan a la Boveda existente: el alta envia el RUC y la URL solo lleva el id del alta", () => {
    const s = sources.onboarding;
    expect(s).toContain("createClientSolCredential(");
    expect(s).toContain("startOnboarding(ruc)");
    expect(sources.api).not.toMatch(/sol_username|sol_password|vault_password/);
    expect(s.match(/router\.replace\(/g)?.length).toBeGreaterThan(0);
    for (const call of s.match(/router\.replace\([^)]*\)/g) ?? []) expect(call).not.toMatch(/solUser|solPassword|vaultPassword|ruc\b/);
    expect(s).not.toMatch(/console\.(log|info|debug)/);
  });
});

describe("destinos y acciones", () => {
  it("el frontend solo envia el enum del destino: nunca URL, selector ni script", () => {
    const s = sources.solApi;
    const code = s.split("\n").filter((line) => !line.trim().startsWith("//")).join("\n"); // sin comentarios
    expect(s).toContain("body.destination = options.destination");
    expect(code).not.toMatch(/\burl\b\s*:|selector|script|href/i);
    expect(sources.solAccessPage).not.toMatch(/sunat\.gob|window\.open|location\.href|<iframe/i);
  });

  it("no hay acciones SUNAT falsas: ni SIRE, ni Declaraciones y Pagos, ni Declara Facil, ni Tramites, ni Buzon", () => {
    for (const [name, source] of Object.entries({ page: sources.solAccessPage, onboarding: sources.onboarding, review: sources.review })) {
      for (const forbidden of [/Abrir SIRE/i, /Declaraciones y Pagos/i, /Declara F[aá]cil/i, /Tr[aá]mites y Consultas/i, /Consultar Buz[oó]n/i, /Descargar constancia/i]) {
        expect(source, `${name} ${forbidden}`).not.toMatch(forbidden);
      }
    }
    expect(sources.solAccessPage).toContain("Abrir {DESTINATION_LABEL[destination]}");
    expect(sources.solAccessPage).toContain("visibleDestinations(");
  });

  it("actualizar la Ficha exige permiso de edicion y arranca con extraccion explicita", () => {
    expect(sources.solAccessPage).toContain("canRefresh && destinations.includes(\"FICHA_RUC\")");
    expect(sources.solAccessPage).toContain('handleStart("FICHA_RUC", true)');
    expect(sources.newClientPage).toContain("canOnboardAssisted(user)");
  });
});

describe("revision humana antes de escribir", () => {
  it("nada se aplica sin confirmar: el panel exige marcar campos y confirmar; sin permiso es solo lectura", () => {
    expect(sources.review).toContain("selection.accepted.length === 0");
    expect(sources.review).toContain("readOnly");
    expect(sources.reviewPage).toContain("readOnly={!canReview}");
    expect(sources.review).not.toMatch(/useEffect\([^)]*onConfirm/);
  });

  it("la pantalla de revision nunca muestra ni edita metadatos internos del negocio", () => {
    for (const field of ["phone", "email", "whatsapp", "monthly_fee", "payment_day", "tags", "notes", "service_status"]) {
      expect(sources.review, field).not.toContain(field);
    }
    expect(sources.model).toContain("OVERRIDABLE_FIELDS");
  });

  it("el alta se finaliza solo desde la confirmacion y puede cancelarse (sin credencial huerfana en el backend)", () => {
    expect(sources.onboarding).toContain("completeOnboarding(onboarding.id");
    expect(sources.onboarding).toContain("cancelOnboarding(");
    expect(sources.onboarding).toContain("Activar al cliente");
  });
});


describe("recuperacion del alta asistida (sesion fallida)", () => {
  it("el reintento reutiliza la credencial guardada y desbloquea la boveda sin volver a pedir la clave SOL", () => {
    const s = sources.onboarding;
    const failed = s.slice(s.indexOf('step === "failed"'));
    expect(failed).toContain("onb-retry-vault-password");
    expect(failed).toContain("<SecureForm");
    expect(failed).not.toMatch(/Clave SOL|onb-sol-password|solPassword/);
    expect(s).toMatch(/await unlockSolVault\(retryVaultPassword\)/);
    expect(s).toMatch(/setRetryVaultPassword\(""\)/); // la contraseña se borra tras usarla
  });
});
