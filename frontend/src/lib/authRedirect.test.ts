import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { loginUrlFor, safeNextPath } from "@/lib/authRedirect";

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf-8");

describe("destino de retorno tras el login", () => {
  it("acepta solo rutas internas, con su consulta", () => {
    expect(safeNextPath("/clients/new?onboarding=04fec347-4f1d-4018-b93b-8b9a01af17f0")).toBe("/clients/new?onboarding=04fec347-4f1d-4018-b93b-8b9a01af17f0");
    expect(safeNextPath("/clients")).toBe("/clients");
    expect(safeNextPath("/cockpit?period=2026-09#top")).toBe("/cockpit?period=2026-09#top");
  });

  it("rechaza redirecciones abiertas, esquemas, rutas de red y bucles hacia el login", () => {
    for (const bad of [
      null, undefined, "", "clients", "//evil.example/x", "/\\evil.example", "https://evil.example/", "javascript:alert(1)", 
      "/login", "/login?next=/clients", "/login/", "/clients\nSet-Cookie: x=1", "/a".repeat(300), "/ok\\..\\x",
    ]) {
      expect(safeNextPath(bad as string | null | undefined), String(bad)).toBeNull();
    }
  });

  it("la URL de login conserva el destino codificado o cae en /login", () => {
    expect(loginUrlFor("/clients/new?onboarding=abc")).toBe("/login?next=%2Fclients%2Fnew%3Fonboarding%3Dabc");
    expect(loginUrlFor("//evil.example")).toBe("/login");
    expect(loginUrlFor(null)).toBe("/login");
  });
});

describe("cableado del retorno (la cookie sigue siendo SameSite=Strict)", () => {
  it("el proxy conserva el destino al redirigir y no relaja nada", () => {
    const proxy = read("src/proxy.ts");
    expect(proxy).toContain("loginUrlFor(destination)");
    expect(proxy).toContain("request.cookies.has(SESSION_COOKIE_NAME)"); // la comprobacion de cookie se mantiene
    expect(proxy).not.toMatch(/NextResponse\.next\(\);\s*}\s*\n\s*return NextResponse\.next/);
  });

  it("el login vuelve al destino seguro y reconoce una sesion ya valida con una peticion del propio sitio", () => {
    const login = read("src/app/login/page.tsx");
    expect(login).toContain("safeNextPath(");
    expect(login).toMatch(/router\.push\(destination\(\)\)/);
    expect(login).toMatch(/fetchCurrentUser\(\)[\s\S]{0,120}router\.replace\(destination\(\)\)/);
    expect(login).not.toMatch(/router\.push\("\/clients"\)/);
  });

  it("una sesion vencida lleva al login conservando el destino (ningun redirect a /login sin destino)", () => {
    const api = read("src/lib/apiClient.ts");
    expect(api).not.toMatch(/window\.location\.href = "\/login"/);
    expect((api.match(/loginUrlFor\(window\.location\.pathname \+ window\.location\.search\)/g) ?? []).length).toBe(3);
  });
});
