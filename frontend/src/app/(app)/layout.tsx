"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { InactivityGuard } from "@/components/security/InactivityGuard";
import { CurrentUserProvider, useCurrentUser } from "@/lib/currentUserContext";

const CHANGE_PASSWORD_REQUIRED_PATH = "/change-password-required";

function AppLayoutInner({ children }: { children: React.ReactNode }) {
  const { user, loading } = useCurrentUser();
  const pathname = usePathname();
  const router = useRouter();
  const [navOpen, setNavOpen] = useState(false);
  const closeNav = useCallback(() => setNavOpen(false), []);
  const toggleNav = useCallback(() => setNavOpen((open) => !open), []);

  useEffect(() => {
    // La propia pantalla de cambio obligatorio queda exenta de esta
    // redirección -- si no, un usuario con must_change_password=true nunca
    // podría llegar a completarla (ciclo de navegación infinito).
    if (pathname === CHANGE_PASSWORD_REQUIRED_PATH) return;
    if (!loading && user?.must_change_password) {
      router.replace(CHANGE_PASSWORD_REQUIRED_PATH);
    }
  }, [loading, user, pathname, router]);

  return (
    <div className="app-shell">
      <Header user={user} navOpen={navOpen} onToggleNav={toggleNav} />
      <div className="app-body">
        <Sidebar open={navOpen} onClose={closeNav} />
        <main className="app-main">{children}</main>
      </div>
      <InactivityGuard />
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <CurrentUserProvider>
      <AppLayoutInner>{children}</AppLayoutInner>
    </CurrentUserProvider>
  );
}
