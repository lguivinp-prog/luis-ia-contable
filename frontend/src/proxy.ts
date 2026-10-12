import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { loginUrlFor } from "@/lib/authRedirect";

const SESSION_COOKIE_NAME = process.env.SESSION_COOKIE_NAME ?? "luis_ia_contable_session";

// En Next.js 16 el archivo "middleware.ts" fue renombrado a "proxy.ts" (ver
// docs/ARCHITECTURE.md). Esta comprobacion solo verifica la PRESENCIA de la
// cookie de sesion; la validez real siempre la resuelve el backend en cada
// peticion (ver core/dependencies.get_current_user en el backend).
export function proxy(request: NextRequest) {
  const hasSessionCookie = request.cookies.has(SESSION_COOKIE_NAME);

  if (!hasSessionCookie) {
    // La cookie es SameSite=Strict: una navegacion iniciada desde otro sitio (enlace de una app/chat/correo) llega SIN cookie aunque la
    // sesion siga vigente. Se conserva el destino (`?next=`, solo ruta interna segura) para volver tras autenticar o al detectar la sesion.
    const destination = request.nextUrl.pathname + request.nextUrl.search;
    return NextResponse.redirect(new URL(loginUrlFor(destination), request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/clients/:path*", "/security/:path*", "/tax-categories/:path*", "/tax-schedules/:path*"],
};
