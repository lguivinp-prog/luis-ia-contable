# Roadmap de fases — Luis IA Contable

Cada fase se implementa de forma independiente, con su propia documentación, migraciones, pruebas y criterios de aceptación. No se avanza a la siguiente fase si la anterior no compila, no inicia o no supera sus pruebas esenciales.

## Bloque transversal — Arranque y sincronización local ✅ (ver ADR-0066)

No pertenece a ninguna fase numerada — es infraestructura operativa que aplica a todas las fases por igual.
Un único punto de entrada (`scripts/start-luis-ia-contable.ps1`, acceso directo de escritorio o tarea opcional
de inicio de sesión de Windows) levanta los servicios, aplica migraciones, sincroniza la información local
(`backend/app/cli/startup_sync.py`, reutilizando `task_generation_service.sync_operational_tasks` sin lógica
de dominio nueva) y abre la interfaz. Exclusión mutua entre dos inicios simultáneos vía advisory lock de
PostgreSQL. Integraciones externas no implementadas (Google Calendar/Drive, SUNAT SOL, Gmail, WhatsApp) se
omiten sin ninguna llamada de red y sin bloquear el arranque local — la ausencia de internet nunca impide usar
el sistema.

## Fase 0 — Fundamentos ✅ (esta entrega)

Documentación base, arquitectura, modelo de amenazas, modelo de datos, estructura de repositorio, Docker Compose, backend/frontend iniciales, health checks, scripts Windows, `.env.example`, CI local (`scripts/test.ps1`).

## Fase 1 — Núcleo operativo (en curso)

**Bloque 1 ✅:** autenticación local, gestión de contribuyentes (CRUD), validación de RUC, proveedor de consulta de RUC simulado, catálogo de obligaciones, asignación de obligaciones por cliente, auditoría básica, datos ficticios, pruebas.

**Bloque 2 ✅ (esta entrega):**
- Endurecimiento de autenticación y sesiones: bloqueo temporal por intentos fallidos con backoff progresivo y mitigación de enumeración por tiempo (ADR-0009), CSRF double-submit cookie (ADR-0010), expiración por inactividad/absoluta, rotación de sesión, límite de sesiones concurrentes, vista de sesiones activas, revocación al cambiar contraseña, UI de advertencia de inactividad.
- Motor de cronogramas tributarios versionado: fuentes/versiones/entradas/excepciones/historial de aprobación/importación de CSV con vista previa y diffs (ADR-0011, ADR-0012, ADR-0014, ADR-0015), categorías tributarias del cliente con vigencia histórica desacopladas de `tax_regime` (ADR-0013), vista previa de vencimiento por cliente/obligación/periodo (sin generación de declaraciones ni eventos de calendario todavía).

**Bloque 3.1 ✅:** periodos tributarios canónicos (`tax_periods`, apertura/cierre), `declarations` con snapshot versionado del cronograma resuelto y 5 tablas de historial (estado, vencimiento, asignación, presentación, reapertura), máquina de estados de flujo/presentación con regresiones auditadas, generación masiva idempotente con detección de vista previa obsoleta (`STALE`), tablero mensual básico.

**Bloque 3.2 ✅ (esta entrega):**
- **Control documental**: catálogo de requisitos híbrido sistema/tenant (ADR-0033), asignación por cliente con vigencia real y vínculo opcional a una obligación concreta (ADR-0034), sincronización idempotente de ítems por declaración —individual y masiva por periodo, sin backfill destructivo— (ADR-0035), integridad cruzada cliente/declaración/requisito vía FKs compuestas (ADR-0036).
- **Determinación y pagos tributarios**: determinación versionada con correcciones (ADR-0038/0039), saldo siempre contra la determinación vigente (ADR-0040), idempotencia de pagos (ADR-0041), concurrencia real con `SELECT ... FOR UPDATE` (ADR-0042), sobrepago con confirmación explícita (ADR-0043).
- **Honorarios profesionales**: acuerdos con vigencia real y ciclo de vida explícito de `supersede`/`cancel` (ADR-0044), cargos con estado persistido separado del riesgo de cobranza calculado en vivo (ADR-0045), recibos con regla de importe agregado (ADR-0046) — dominio completamente independiente del tributario (raíz de bloqueo propia, sin importar `declaration_service`).
- **Cierre operativo completo**: cierre normal (tres precondiciones: presentación/documentos/impuesto) y cierre excepcional con pendientes, gateado por `settings.allow_close_with_pending_tax` + rol admin + motivo explícito (ADR-0047).
- **Tablero y riesgo extendidos**: `computed_risk_status` con 3 valores nuevos (`CLOSED_WITH_PENDING`/`PENDING_TAX`/`PENDING_DOCUMENTS`), agregados de impuestos (1:1 por declaración) y honorarios (por cliente/periodo, repetidos nunca multiplicados) sin duplicar filas del tablero (ADR-0048).
- **Reportes**: exportación CSV/Excel de 10 tipos de datos, sanitizada contra *CSV formula injection*, límite de filas configurable, `openpyxl` como dependencia declarativa (ADR-0049).
- Frontend completo para las pantallas anteriores (pestañas de declaración, honorarios por cliente, tablero extendido, reportes).

**Bloque 4 ✅ (esta entrega): RBAC real + gestión de usuarios**
- **Catálogo de permisos**: `Permission` (30 valores, `backend/app/core/permissions.py`) + `ROLE_PERMISSIONS`
  declarativo por rol (`admin`=30, `contador`=27, `asistente`=13), fallo cerrado ante rol/permiso desconocido
  (ADR-0050). Separación `document:item_write` (operar ítems ya asignados, incluido `asistente`) vs.
  `document:manage` (configuración documental: catálogo, asignación, sincronización — solo `admin`/`contador`).
- **Gestión de usuarios**: alta con contraseña temporal (`must_change_password`), cambio de rol, desactivación
  y reactivación con motivo, restablecimiento administrativo de contraseña, revocación de sesiones — 6
  endpoints (`/api/v1/users/*`), protección concurrente real del último admin activo por tenant vía
  `SELECT ... FOR UPDATE` con orden determinista (sin deadlock), migración 0012 con preflight de 3
  verificaciones antes de cualquier DDL (ADR-0051).
- **Aplicación de permisos**: `require_permission` sustituye a `ensure_admin_role` en los 15 routers
  existentes, con cobertura estructural verificada por prueba dedicada (allowlist cerrada de rutas exentas,
  nunca por prefijo) y matriz funcional 200/403 de los 30 permisos (ADR-0052).
- **Frontend**: `CurrentUserProvider`/`useCurrentUser` como fuente única de permisos (`GET /auth/me`),
  pantalla `/users` de administración, pantalla obligatoria de cambio de contraseña, gateo completo (ocultar
  o deshabilitar, nunca solo por color) de los 16 componentes/páginas mutantes existentes.

**Bloque 5 ✅ (esta entrega): Almacenamiento real y seguro de documentos**
- **Modelo y almacenamiento**: tabla `declaration_document_files` (migración 0013) con cadena lógica de
  versiones (`logical_file_id`/`version`), índice único parcial que impone una sola versión `ACTIVE` por
  cadena (varias cadenas pueden coexistir activas en el mismo ítem), `FileStorageBackend`/
  `LocalFileStorageBackend` desacoplado sobre un volumen Docker nombrado fuera del árbol de código (ADR-0053).
- **Ciclo de vida sin restauración**: `ACTIVE`/`SUPERSEDED`/`DELETED` con transiciones sin ambigüedad de
  estado; `file_metadata` legado se conserva sin cambios de esquema como notas descriptivas heredadas, nunca
  evidencia física (ADR-0054).
- **Consistencia BD-disco y concurrencia real**: orden estricto escritura-validación-rename-inserción,
  compensación física ante fallo de `flush()`/`commit()`, bloqueo del `file_id` exacto (no "cualquier ACTIVE de
  la cadena") verificado con dos conexiones reales — incluido un hallazgo real de `populate_existing=True`
  necesario para que el bloqueo no lea un objeto cacheado obsoleto —, reporte de integridad con 5 categorías
  exactas sin exponer nunca `storage_key` (ADR-0055).
- **Seguridad de contenido y descarga**: validación por contenido real (incluida verificación estructural
  OOXML contra ZIP-bombs y macros, nunca por extensión declarada), nombre original saneado, `Content-Disposition`
  seguro con codificación RFC 6266, permiso `document:file_manage` separado para reemplazar/eliminar (ADR-0056).
- **Backup/restauración conjunta**: `backup.ps1`/`restore.ps1` con backend detenido durante la captura,
  verificación del 100% de los blobs (nunca una muestra), manifiesto con SHA-256 de dump y volumen, snapshot
  de seguridad automático antes de restaurar con reversión automática si la verificación post-restauración
  falla.
- **Frontend**: sub-sección de archivos por cadena en cada ítem documental (`DocumentItemFiles.tsx`), subir/
  descargar/reemplazar/eliminar gateados por permiso, verificado manualmente con los 3 roles reales.

**Exclusión de alcance (decisión funcional del propietario):** Luis IA Contable no incorporará un módulo
independiente de rectificatorias tributarias vinculado a declaraciones originales — no forma parte del flujo
operativo real del estudio. Esta exclusión no afecta la funcionalidad ya existente: las correcciones de
determinaciones tributarias, la corrección administrativa de presentación, los historiales de estado y la
auditoría asociada se conservan tal como están implementadas (ver ADR-0038/0039 y `docs/USER_MANUAL.md`).

## Fase 2 — Gestión operativa interna: tareas, agenda y alertas ✅ (esta entrega)

**Bloque 1 ✅: tareas operativas, responsables, agenda interna y alertas derivadas**
- **Modelo híbrido**: `operational_tasks` materializadas (ciclo de vida propio, comentarios, historial) +
  `OVERDUE`/baldes de agenda siempre calculados en vivo, nunca persistidos — mismo principio que
  `declaration_risk.py`/`professional_fee_risk.py` (ADR-0057). 5 formas estructurales de FK compuesta en vez
  de `source_type`/`source_id`; cadena de 2 FK verificada `charge → agreement → client` para honorarios.
- **Ciclo de vida y concurrencia**: 4 estados sin reapertura (`PENDING`/`IN_PROGRESS`/`COMPLETED`/
  `CANCELLED`), bloqueo de fila exacta (`populate_existing=True`) + versión optimista para toda acción
  humana, orden global determinista de 12 fases para evitar interbloqueos entre sincronizaciones
  concurrentes, rechazo explícito de mutaciones sin cambio real (`409 TASK_NO_CHANGES`) (ADR-0058).
- **Generación automática idempotente**: 3 reglas (`DECLARATION_FILING`, `DOCUMENT_COLLECTION` — incluye
  `OBSERVED`, `FEE_COLLECTION`) vía `ON CONFLICT DO NOTHING RETURNING`, resolución automática con mapeo
  exhaustivo y precedencia explícita, realineación de vencimiento, CLI multitenant explícito
  (`--tenant-id` obligatorio, transacción propia) que comparte servicio con `POST /tasks/sync` — cero lógica
  duplicada (ADR-0059).
- **Agenda y alertas**: helper centralizado de zona horaria (`America/Lima`), semántica única de "vencido"
  sin variante local en ninguna capa, filtros canónicos `time_bucket`/`assigned_to` en `GET /tasks`, alertas
  100% derivadas sin tabla propia y sin doble conteo de honorarios ya materializados en tareas
  `FEE_COLLECTION` (ADR-0060).
- **Permisos**: 8 permisos nuevos (`task:read/create/update/complete/assign/cancel/sync`, `alert:read`),
  `task:sync` concedido a los 3 roles; autorización por fila del asistente (única excepción del repositorio
  al patrón "permiso de rol, nunca de fila"), incluida la carrera comentario-vs-reasignación resuelta con el
  mismo bloqueo de fila (ADR-0061).
- **Frontend**: `/tasks`, `/tasks/[id]`, `/tasks/new` (con selector opcional de cliente para tareas
  manuales), `/agenda`, extensión aditiva del tablero mensual con `task_summary` — ningún campo tributario
  existente cambia.
- **Reportes**: 11º tipo de reporte (`TASKS`), cliente siempre resuelto de forma denormalizada.

## Fase 3 — Google y comunicaciones (en desarrollo)

OAuth Google, Google Calendar, Google Drive (estructura documental), WhatsApp asistido, plantillas de mensajes, sincronización, copias cifradas en Drive.

Dividida en 4 bloques incrementales. **Reordenamiento formal de ejecución** (ver ADR-0067): dado que el
usuario todavía no dispone de un proyecto Google Cloud, el Bloque de comunicaciones asistidas por WhatsApp —
que no depende de Google en absoluto — se adelanta y pasa a ser el **Bloque 3.2 real** (migración `0016`);
Calendar y Drive quedan **diferidos**, conservando su contenido funcional intacto, y se renumeran como
Bloque 3.3 (futura migración `0017`) y Bloque 3.4 (futura migración `0018`) respectivamente. Este es un
reordenamiento de secuencia de ejecución, nunca una eliminación de alcance: Calendar y Drive siguen
formando parte de la Fase 3 tal como se planificaron.

- **Bloque 3.1 ✅** (etiqueta `v0.7.0-alpha.1`, migración `0015`, ver `docs/DECISIONS/ADR-0062` a `ADR-0065`)
  — fundamento de integraciones y conexión OAuth Google de solo identidad (`openid`+`email`), sin Calendar/
  Drive todavía. Cerrado como infraestructura **preparada pero desactivada por configuración**
  (`GOOGLE_LIVE_VALIDATION_DEFERRED`, ver ADR-0063): sin `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`
  reales, el sistema reporta `configured=false` y el resto de la aplicación funciona con normalidad; el spike
  de PKCE real y el smoke test contra una cuenta Google real quedan diferidos hasta que el usuario disponga
  de un proyecto Google Cloud, y son obligatorios antes de habilitar Calendar/Drive.
- **Bloque 3.2** (migración `0016`, ver ADR-0067) — comunicaciones asistidas por WhatsApp mediante enlace
  `wa.me`: borradores editables con plantillas versionadas en código, apertura manual en WhatsApp
  (`OPEN_REQUESTED`) y confirmación manual de envío (`MARKED_SENT`, siempre una declaración humana, nunca
  verificada por el sistema) — sin WhatsApp Business API, sin WhatsApp Cloud API, sin automatización ni
  scraping de ningún tipo, sin envío automatizado. No depende de Google ni de internet para preparar
  mensajes.
- **Bloque 3.3 — diferido** (futura migración `0018`; próximo ADR disponible, número 0072, se creará recién
  cuando se implemente) — autorización incremental de Calendar y sincronización unidireccional de tareas.
  *(Los números de migración/ADR previstos originalmente para 3.3/3.4 — `0017`/ADR-0068 y ADR-0069 — quedaron
  ocupados por Fase 5/Bloque 5.1, implementado antes; ver más abajo.)*
- **Bloque 3.4 — diferido** (futura migración `0019`; siguiente ADR disponible, número 0073, se creará recién
  cuando se implemente) — autorización incremental de Drive y exportación explícita de documentos.

## Fase 4 — Gmail y notificaciones (pendiente)

Gmail de solo lectura, filtros y asociación por RUC, descarga controlada de adjuntos, bandeja de notificaciones, niveles de riesgo, expedientes, IA opcional para clasificación/resumen (nunca como única fuente de verdad).

## Fase 5 — Bóveda y acceso SOL supervisado (bloque 5.1 ✅, bloque 5.2 pendiente)

- **Bloque 5.1 — implementado** (migración `0017`, ver ADR-0068 a ADR-0071) — bóveda criptográfica productiva
  de credenciales SOL por cliente: jerarquía de tres niveles (slot KEK → `vault_root_key` → `credential_dek` →
  credencial), Argon2id + AES-256-GCM (sin DPAPI: se usa el mismo patrón de sobre versionado + AAD ya
  productivo en `app/core/integration_crypto.py`, portable y sin atarse a una API específica de Windows),
  recuperación en dos pasos con código mostrado una sola vez, invitaciones de dos actores, rotación ordinaria
  de `credential_dek`, `VaultUnlockRegistry` en memoria de un único proceso (bloqueo automático al reiniciar
  el backend). Toda credencial nueva/actualizada queda `NEEDS_VALIDATION` — este bloque **no** implementa
  Playwright, acceso a SUNAT, Buzón SOL ni sesiones de acceso SOL reales.
- **Bloque 5.2 — diferido** (no diseñado todavía) — automatización de navegador con Playwright estrictamente
  supervisada (consulta de régimen, buzón, descargas — nunca presentación ni pago), detención automática ante
  CAPTCHA o cambios inesperados. Depende de que el Bloque 5.1 ya esté cerrado e integrado.

## Fase 6 — SIRE y controles especializados (pendiente)

Adaptadores para RVIE, RCE, AFPnet, EsSalud, ITAN, Renta Anual 710, fraccionamientos, contratos, poderes. Nunca presentación ni aceptación automática de registros.

## Fase 7 — Multiusuario y comercialización (pendiente)

Tenants, usuarios, roles, permisos, separación de datos real entre despachos, portal web en la nube, número empresarial y WhatsApp Cloud API, políticas de privacidad, exportación/eliminación de datos, despliegue comercial.

## Criterios para avanzar de fase

1. La fase actual compila, arranca (`docker compose up`) y pasa sus pruebas esenciales (`scripts/test.ps1`).
2. La documentación de la fase (requisitos, diseño técnico, pruebas, manual de uso, ADRs relevantes) está actualizada.
3. No hay secretos ni datos reales expuestos en el repositorio.
4. Se ha registrado en `docs/CHANGELOG.md` el cierre de la fase.
