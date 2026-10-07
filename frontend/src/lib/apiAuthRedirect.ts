// Un 401 normalmente significa sesion vencida (el cliente redirige a /login). Excepcion: errores de DOMINIO que el
// backend modela como 401 pero que NO invalidan la sesion de aplicacion -- p. ej. contrasena de boveda incorrecta.
const DOMAIN_UNAUTHORIZED_CODES: ReadonlySet<string> = new Set(["SOL_VAULT_BAD_PASSWORD"]);

export function isDomainUnauthorizedBody(body: unknown): boolean {
  if (!body || typeof body !== "object") return false;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return false;
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" && DOMAIN_UNAUTHORIZED_CODES.has(code);
}
