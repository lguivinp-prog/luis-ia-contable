"use client";

import { useEffect } from "react";
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
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <Header user={user} />
      <div style={{ display: "flex", flex: 1 }}>
        <Sidebar />
        <main style={{ flex: 1, minWidth: 0, padding: "1.5rem" }}>{children}</main>
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
