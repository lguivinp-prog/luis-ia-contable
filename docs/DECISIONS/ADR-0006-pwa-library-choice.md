# ADR-0006: Service worker manual en lugar de Serwist

## Estado
Aceptado (revisado durante la implementación)

## Contexto

La aplicación debe ser instalable como PWA en Windows y usable desde celular. La decisión original (ver historial) era usar [Serwist](https://serwist.pages.dev/), sucesor mantenido de `next-pwa`, para generar el service worker.

Durante la implementación se creó el proyecto con `create-next-app@latest`, que instaló **Next.js 16.2.11**. A partir de Next.js 16, **Turbopack es el compilador por defecto tanto para `next dev` como para `next build`**, y si el proyecto define una configuración `webpack` personalizada, `next build` **falla explícitamente** para evitar builds mal configurados (solo se puede continuar forzando `--webpack`, renunciando a Turbopack, o `--turbopack` ignorando el config de webpack).

`@serwist/next` (y su dependencia `@serwist/webpack-plugin`) inyecta configuración de Webpack en `next.config`, por lo que es incompatible con Turbopack en este momento. Adoptarlo habría obligado a renunciar a Turbopack (compilador recomendado y por defecto en Next 16) solo para tener un service worker.

## Decisión

Se implementa un **service worker escrito a mano** (`public/sw.js`), minimalista, registrado desde un componente cliente en `src/app/layout.tsx`. Cubre:
- Cache-first de assets estáticos de la PWA (íconos, manifest).
- Sin estrategia de cache para llamadas a `/api/*` (siempre red, nunca se cachean datos tributarios).
- `public/manifest.json` con nombre, íconos, `display: standalone`, colores del tema.

No se usa ninguna librería de terceros para el service worker en esta fase.

## Consecuencias

- Se pierde la conveniencia de precache automático de Serwist basado en el manifiesto de build; se acepta como costo menor para esta fase (Fase 0/1), dado que la app es mayormente dinámica (datos tributarios, no debe cachearse agresivamente).
- Si en una fase futura Serwist (u otra librería) añade soporte estable para Turbopack, se puede reevaluar esta decisión con un nuevo ADR.
- Mantener el service worker a mano requiere disciplina para no cachear respuestas de `/api/*` por error; esto queda documentado también en `docs/SECURITY_MODEL.md`.
