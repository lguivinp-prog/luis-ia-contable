"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { fetchCurrentUser } from "@/lib/auth";
import type { User } from "@/lib/types/auth";

interface CurrentUserContextValue {
  user: User | null;
  loading: boolean;
  refreshCurrentUser: () => Promise<void>;
}

const CurrentUserContext = createContext<CurrentUserContextValue | null>(null);

export function CurrentUserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshCurrentUser = useCallback(async () => {
    try {
      const fresh = await fetchCurrentUser();
      setUser(fresh);
    } catch {
      // apiClient ya redirige a /login ante un 401
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Misma forma que el layout original (.then/.catch directo, sin pasar por
    // una funcion async invocada dentro del efecto) para evitar cascadas de
    // renders sincronas dentro del cuerpo del efecto.
    fetchCurrentUser()
      .then(setUser)
      .catch(() => {
        // apiClient ya redirige a /login ante un 401
      })
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo(
    () => ({ user, loading, refreshCurrentUser }),
    [user, loading, refreshCurrentUser],
  );

  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}

export function useCurrentUser(): CurrentUserContextValue {
  const context = useContext(CurrentUserContext);
  if (!context) {
    throw new Error("useCurrentUser debe usarse dentro de CurrentUserProvider");
  }
  return context;
}
