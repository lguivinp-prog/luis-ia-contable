// Service worker minimalista para Luis IA Contable (ver docs/DECISIONS/ADR-0006).
// No cachea NUNCA respuestas de /api/*: los datos tributarios siempre deben
// venir de la red. Solo aplica cache-first a assets estaticos de la PWA.

const CACHE_NAME = "luis-ia-contable-static-v1";
const STATIC_ASSETS = ["/manifest.json", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) {
    return;
  }

  if (STATIC_ASSETS.includes(url.pathname)) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached ?? fetch(event.request))
    );
  }
});
