"use client";

import { useEffect } from "react";

export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window !== "undefined" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // El registro del service worker es una mejora progresiva (PWA);
        // si falla, la aplicacion sigue funcionando normalmente.
      });
    }
  }, []);

  return null;
}
