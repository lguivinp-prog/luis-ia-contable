// Destino de retorno tras el login. La cookie de sesion es SameSite=Strict (proteccion que se conserva): el navegador NO la envia en
// navegaciones iniciadas desde OTRO sitio (un enlace pulsado desde una app, un correo o un chat), de modo que la primera peticion a una
// pagina protegida llega sin cookie y el proxy redirige a /login aunque la sesion siga vigente. Por eso el destino viaja en `?next=`,
// y el login (que consulta la sesion con una peticion del propio sitio, que SI lleva la cookie) vuelve a el automaticamente.

const MAX_NEXT_LENGTH = 400;

/** Solo rutas internas relativas. Rechaza esquemas, `//host`, `/\host`, caracteres de control y bucles hacia /login. */
export function safeNextPath(raw: string | null | undefined): string | null {
  if (!raw || raw.length > MAX_NEXT_LENGTH) return null;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return null;
  if (/[\u0000-\u001f\u007f\\]/.test(raw)) return null;
  if (/^\/login(\/|\?|#|$)/.test(raw)) return null;
  try {
    const parsed = new URL(raw, "http://internal.invalid");
    if (parsed.origin !== "http://internal.invalid") return null;
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return null;
  }
}

/** URL de login que conserva el destino solicitado (si es seguro). */
export function loginUrlFor(path: string | null | undefined): string {
  const next = safeNextPath(path);
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}
