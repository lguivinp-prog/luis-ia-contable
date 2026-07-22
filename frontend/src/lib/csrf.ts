// Lee el token CSRF de la cookie no-HttpOnly `csrf_token` (double-submit
// cookie, ver docs/DECISIONS/ADR-0010-csrf-protection.md). Nunca se guarda en
// localStorage; solo vive en la cookie que el backend establece.
export function readCsrfTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}
