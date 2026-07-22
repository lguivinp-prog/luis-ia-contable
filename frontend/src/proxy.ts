import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SESSION_COOKIE_NAME = process.env.SESSION_COOKIE_NAME ?? "luis_ia_contable_session";

// En Next.js 16 el archivo "middleware.ts" fue renombrado a "proxy.ts" (ver
// docs/ARCHITECTURE.md). Esta comprobacion solo verifica la PRESENCIA de la
// cookie de sesion; la validez real siempre la resuelve el backend en cada
// peticion (ver core/dependencies.get_current_user en el backend).
export function proxy(request: NextRequest) {
  const hasSessionCookie = request.cookies.has(SESSION_COOKIE_NAME);

  if (!hasSessionCookie) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/clients/:path*", "/security/:path*", "/tax-categories/:path*", "/tax-schedules/:path*"],
};
