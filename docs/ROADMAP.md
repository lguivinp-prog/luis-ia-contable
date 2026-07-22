# Roadmap de fases — Luis IA Contable

Cada fase se implementa de forma independiente, con su propia documentación, migraciones, pruebas y criterios de aceptación. No se avanza a la siguiente fase si la anterior no compila, no inicia o no supera sus pruebas esenciales.

## Fase 0 — Fundamentos ✅ (esta entrega)

Documentación base, arquitectura, modelo de amenazas, modelo de datos, estructura de repositorio, Docker Compose, backend/frontend iniciales, health checks, scripts Windows, `.env.example`, CI local (`scripts/test.ps1`).

## Fase 1 — Núcleo operativo (en curso)

**Bloque 1 ✅ (esta entrega):** autenticación local, gestión de contribuyentes (CRUD), validación de RUC, proveedor de consulta de RUC simulado, catálogo de obligaciones, asignación de obligaciones por cliente, auditoría básica, datos ficticios, pruebas.

**Bloques siguientes (pendientes):**
- Motor de cronogramas tributarios versionado (importación CSV, aprobación humana, generación de vencimientos).
- Control de declaraciones por contribuyente/obligación/periodo (estados, transiciones).
- Control documental mensual (lista de documentos requeridos por cliente).
- Honorarios profesionales (facturación, cobranza, reportes).
- Panel principal, matriz mensual, reportes CSV/Excel.
- Bloqueo de cuenta por intentos fallidos e inactividad.

## Fase 2 — Google y comunicaciones (pendiente)

OAuth Google, Google Calendar, Google Drive (estructura documental), WhatsApp asistido, plantillas de mensajes, sincronización, copias cifradas en Drive.

## Fase 3 — Gmail y notificaciones (pendiente)

Gmail de solo lectura, filtros y asociación por RUC, descarga controlada de adjuntos, bandeja de notificaciones, niveles de riesgo, expedientes, IA opcional para clasificación/resumen (nunca como única fuente de verdad).

## Fase 4 — Bóveda y acceso SOL supervisado (pendiente)

Bóveda criptográfica de credenciales (AES-256-GCM + Argon2id + DPAPI), automatización de navegador con Playwright estrictamente supervisada (consulta de régimen, buzón, descargas — nunca presentación ni pago), detención automática ante CAPTCHA o cambios inesperados.

## Fase 5 — SIRE y controles especializados (pendiente)

Adaptadores para RVIE, RCE, AFPnet, EsSalud, ITAN, Renta Anual 710, fraccionamientos, contratos, poderes. Nunca presentación ni aceptación automática de registros.

## Fase 6 — Multiusuario y comercialización (pendiente)

Tenants, usuarios, roles, permisos, separación de datos real entre despachos, portal web en la nube, número empresarial y WhatsApp Cloud API, políticas de privacidad, exportación/eliminación de datos, despliegue comercial.

## Criterios para avanzar de fase

1. La fase actual compila, arranca (`docker compose up`) y pasa sus pruebas esenciales (`scripts/test.ps1`).
2. La documentación de la fase (requisitos, diseño técnico, pruebas, manual de uso, ADRs relevantes) está actualizada.
3. No hay secretos ni datos reales expuestos en el repositorio.
4. Se ha registrado en `docs/CHANGELOG.md` el cierre de la fase.
