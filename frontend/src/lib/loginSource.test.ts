// Regresion del defecto "formulario que envia secretos de forma nativa": antes de la hidratacion de React (bundle sin cargar,
// JavaScript deshabilitado, HMR bloqueado) un envio HTML nativo ponia correo y contraseña en la URL, en el historial y en
// los logs del servidor. La proteccion es UN componente reutilizable (`SecureForm`) y estas pruebas impiden que una pagina con
// campos de contraseña use un `<form>` crudo. Barrido estatico (vitest = Node, sin render); el comportamiento se verifica en
// Chromium real con `m2-live/ui_form_security_check.py`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const root = join(process.cwd(), "src");
const read = (path: string) => readFileSync(join(process.cwd(), path), "utf-8");

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return tsxFiles(full);
    return full.endsWith(".tsx") && !full.endsWith(".test.tsx") ? [full] : [];
  });
}

describe("SecureForm (proteccion reutilizable contra envios nativos)", () => {
  const secure = read("src/components/ui/SecureForm.tsx");

  it("deshabilita todos los controles hasta la hidratacion (renderizado por el servidor)", () => {
    expect(secure).toMatch(/useSyncExternalStore\(subscribeNever, \(\) => true, \(\) => false\)/); // servidor: false
    expect(secure).toMatch(/<fieldset\s+disabled=\{!hydrated\}/);
  });

  it("intercepta SIEMPRE el envio, ignora el envio previo a la hidratacion y usa POST", () => {
    expect(secure).toMatch(/event\.preventDefault\(\);\s*if \(!hydrated\) return;/);
    expect(secure).toContain('method="post"');
    expect(secure).not.toMatch(/method="get"/i);
  });
});

describe("ninguna pagina con secretos usa un formulario nativo", () => {
  it("todo archivo con un campo de contraseña usa SecureForm y ningun <form> crudo", () => {
    const offenders: string[] = [];
    let secured = 0;
    for (const file of tsxFiles(root)) {
      const source = readFileSync(file, "utf-8");
      if (!/type="password"|<DeliberateInput\s+secret/.test(source) || /(SecureForm|DeliberateInput)\.tsx$/.test(file)) continue;
      if (/<form\b/.test(source)) offenders.push(`${file}: <form> crudo`);
      if (!/<SecureForm\b/.test(source)) offenders.push(`${file}: sin SecureForm`);
      secured += (source.match(/<SecureForm\b/g) ?? []).length;
    }
    expect(offenders).toEqual([]);
    expect(secured).toBeGreaterThanOrEqual(11); // login, alta asistida, credencial SOL, boveda (5), usuarios, cambio de clave...
  });

  it("el login se explica en pantalla y no vacia los campos por un error", () => {
    const login = read("src/app/login/page.tsx");
    expect(login).toContain("<SecureForm");
    expect(login).toContain("describeLoginError(err)");
    expect(login).not.toMatch(/setPassword\(""\)|setEmail\(""\)/);
  });
});

describe("el servidor de desarrollo admite la IP de loopback", () => {
  it("allowedDevOrigins incluye 127.0.0.1", () => {
    expect(read("next.config.ts")).toMatch(/allowedDevOrigins:\s*\[[^\]]*"127\.0\.0\.1"/);
  });
});

// --- Aislamiento de credenciales: el autocompletado del navegador nunca rellena campos SOL / de boveda ---
import { isDeliberateInput } from "@/components/ui/DeliberateInput";

describe("campos SOL y de boveda (entrada deliberada)", () => {
  const files = [
    "src/components/clients/AssistedOnboarding.tsx",
    "src/app/(app)/clients/[id]/sol-credential/page.tsx",
    "src/app/(app)/clients/[id]/sol-access/page.tsx",
    "src/app/(app)/settings/sol-vault/page.tsx",
  ];

  it("ningun campo SOL/boveda es un <Input> comun ni un type=password suelto", () => {
    for (const file of files) {
      const source = read(file);
      expect(source, file).toContain("<DeliberateInput");
      expect(source, file).not.toMatch(/<Input\b[^>]*type="password"/);
      expect(source, file).not.toMatch(/<Input\b[^>]*label="(Usuario SOL|Contraseña SOL|Clave SOL)"/);
    }
  });

  it("el componente no depende de autocomplete=off: name unico, new-password, solo lectura hasta enfocar y solo entrada humana", () => {
    const component = read("src/components/ui/DeliberateInput.tsx");
    expect(component).toContain('autoComplete={secret ? "new-password" : "off"}');
    expect(component).toMatch(/name=\{`f-\$\{uid/);
    expect(component).toContain("readOnly={!armed}");
    expect(component).toContain("isDeliberateInput");
    expect(component).not.toMatch(/autoComplete="(username|current-password)"/);
  });

  it("solo los eventos de una persona cuentan; el autocompletado y el relleno programatico se rechazan", () => {
    for (const human of ["insertText", "insertFromPaste", "insertFromDrop", "insertCompositionText", "deleteContentBackward", "historyUndo"]) {
      expect(isDeliberateInput(human), human).toBe(true);
    }
    for (const automatic of [undefined, null, "", "insertReplacementText", "formatBold"]) {
      expect(isDeliberateInput(automatic as string | undefined), String(automatic)).toBe(false);
    }
  });
});
