"use client";

import { useEffect, useState } from "react";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { InactivityGuard } from "@/components/security/InactivityGuard";
import { fetchCurrentUser } from "@/lib/auth";
import type { User } from "@/lib/types/auth";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    fetchCurrentUser()
      .then(setUser)
      .catch(() => {
        // apiClient ya redirige a /login ante un 401
      });
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <Header user={user} />
      <div style={{ display: "flex", flex: 1 }}>
        <Sidebar />
        <main style={{ flex: 1, padding: "1.5rem" }}>{children}</main>
      </div>
      <InactivityGuard />
    </div>
  );
}
