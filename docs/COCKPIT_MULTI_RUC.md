# Cockpit Multi-RUC mensual y workspace cliente/periodo (Milestone M1)

Centro de control operativo: el contador elige un periodo tributario y ve **todos los clientes activos en una sola vista**,
una fila por cliente, y puede abrir el **workspace** de un cliente para ese periodo. No sustituye a CONTASIS SQL, DARWIN IA ni
SUNAT/SIRE: no reconstruye su lógica contable ni escribe en sistemas externos.

Qué es **derivado**, qué es **manual** y qué es **futuro**:

| Tipo | Qué | Dónde vive |
|---|---|---|
| Derivado (no se persiste) | documentos, SIRE, declaraciones, impuesto, honorarios, vencimientos, riesgo, fase mensual | calculado en cada lectura desde los dominios existentes |
| Manual (único estado nuevo persistido) | **Buzón**, **Contabilidad**, **Comunicación al cliente**, notas del periodo | `client_period_operations` (+ historial), migración 0019 |
| Futuro (no implementado) | sincronización del Buzón, Ficha RUC, navegación SOL posterior al aterrizaje, accesos directos a SIRE/Declaraciones y Pagos, notificaciones SUNAT/SUNAFIL, conciliación bancaria, portal del cliente, constancias, WhatsApp Business/API, Gmail/Calendar/Drive, CONTASIS SQL, DARWIN | ver `docs/ROADMAP.md` |

## API

| Endpoint | Permiso | Notas |
|---|---|---|
| `GET /api/v1/cockpit/monthly?tax_period_id=…` | `dashboard:read` | lista (una fila por cliente) + tarjetas; solo lectura |
| `GET /api/v1/cockpit/clients/{client_id}/periods/{period_id}` | `dashboard:read` | agregado del workspace; solo lectura; sin secretos |
| `GET /api/v1/cockpit/clients/{client_id}/periods/{period_id}/history` | `dashboard:read` | historial de cambios manuales |
| `PUT /api/v1/cockpit/clients/{client_id}/periods/{period_id}/operations` | **`cockpit:update`** | estados manuales; `extra=forbid`; CSRF; `expected_row_version` |

`cockpit:update` es un permiso nuevo (admin sí, contador sí, asistente **no**): admin 57, contador 49, asistente 22 permisos.
No reutiliza `declaration:write` ni `task:update`. El backend es la autoridad; la UI solo oculta/deshabilita controles.

UI: `/cockpit` (lista) y `/cockpit/[clientId]?period=<id>&tab=<pestaña>` (workspace). El periodo y la pestaña viven en la URL
(refresh, atrás y adelante funcionan). `/dashboard/monthly` y el Tablero mensual **no cambian**.

## Granularidad: una fila por (tenant, cliente, periodo)

La población base son los **clientes activos** (`service_status = activo`), no las declaraciones. Un cliente con 621, PLAME,
SIRE-RVIE y SIRE-RCE aparece **una sola vez**; el detalle por obligación es drill-down. Un cliente activo **sin declaraciones**
en el periodo aparece con `period_generated=false`, `overall_risk=PERIOD_NOT_GENERATED` y enlace a *Generar declaraciones*
(ningún endpoint de lectura genera declaraciones). La paginación es por **cliente**.

## Agregación derivada

- **Declaraciones aplicables**: no canceladas y no `NOT_APPLICABLE`. `pendientes` = aplicables sin `filing_status` FILED/NOT_REQUIRED.
- **Vence** (`nearest_due_date`): mínimo `current_effective_due_date` entre las pendientes (si no hay pendientes, entre las aplicables).
- **Riesgo**: se reutiliza `declaration_risk.compute_risk_status` por declaración (única implementación). `overall_risk` es el más
  severo entre los riesgos de las declaraciones y los componentes (documentos incompletos → `PENDING_DOCUMENTS`, pago de impuesto
  pendiente → `PENDING_TAX`, cobranza vencida o que vence hoy → `PENDING_FEES`). Orden de severidad:
  `CRITICAL > OVERDUE > DUE_TODAY > PENDING_TAX > PENDING_DOCUMENTS > PERIOD_NOT_GENERATED > CLOSED_WITH_PENDING > PENDING_FEES >
  UPCOMING > ON_TRACK > CLOSED > NOT_APPLICABLE`. La cobranza de honorarios **nunca** hace parecer menos grave una obligación legal.
  **Los estados manuales NO participan en el riesgo.**
- **Documentos**: una sola declaración aplicable incompleta impide `COMPLETE` (peor estado gana).
- **Impuesto**: suma de liquidaciones **vigentes** y pagos **no anulados**; `tax_balance = pagable − pagado`; sin liquidación los
  montos son `null` (no 0). `tax_payment_status` toma el peor estado entre declaraciones; `NOT_DETERMINED` se ignora mientras
  alguna declaración tenga un estado determinado.
- **Honorarios**: agregado existente por (cliente, periodo); nunca se multiplica por el número de declaraciones.

## SIRE (derivado, no persistido)

| Situación | Estado SIRE |
|---|---|
| sin obligación SIRE asignada | `NOT_CONFIGURED` (no es "no aplica") |
| SIRE configurado y **periodo sin generar** | **`NOT_STARTED`** (nunca `null`; `period_generated=false`) |
| workflow `PENDING` / configurado sin declaración vigente en periodo generado | `NOT_STARTED` |
| `DOCUMENTS_REQUESTED`, `INFORMATION_INCOMPLETE`, `DOCUMENTS_COMPLETE`, `IN_PROGRESS`, `UNDER_REVIEW`, o filing `FAILED` | `IN_PROGRESS` |
| `READY_TO_FILE` o filing `READY` | `READY_FOR_SUNAT` |
| filing `FILED` | `PRESENTED` |
| workflow `NOT_APPLICABLE` o filing `NOT_REQUIRED` | `NOT_APPLICABLE` |

RVIE y RCE se combinan de forma conservadora: gana el **más pendiente** (p. ej. RVIE presentado + RCE sin iniciar ⇒ `NOT_STARTED`).
*Corrección de M1:* en Domain B, "configurado con el periodo sin generar" devolvía `null`; ahora es `NOT_STARTED`.

## Fase mensual (derivada)

El trabajo de un periodo ocurre en el mes calendario siguiente (fecha de Lima): día 1–4 `APERTURA`, 5–14 `OPERACION`, ≥15 `CIERRE`;
antes de ese mes `PRE_APERTURA`; pasado ese mes sigue `CIERRE`. La fecha legal de vencimiento no se reemplaza.

## Estados manuales (persistidos)

| Campo | Valores | Defecto (sin fila) |
|---|---|---|
| Buzón | `NOT_REVIEWED`, `REVIEWED_CLEAR`, `REVIEWED_ACTION_REQUIRED`, `NOT_APPLICABLE` | `NOT_REVIEWED` |
| Contabilidad | `NOT_STARTED`, `IN_PROGRESS`, `REVIEWED`, `COMPLETED`, `NOT_APPLICABLE` | `NOT_STARTED` |
| Comunicación al cliente | `NOT_STARTED`, `PREPARED`, `SENT`, `NOT_APPLICABLE` | `NOT_STARTED` |
| Notas | texto ≤ 2000 o `null` | `null` |

- **Contabilidad** es solo el estado operativo del trabajo hecho fuera: *"El detalle contable permanece en CONTASIS/DARWIN."* No hay asientos, libros ni balances.
- **Buzón** es una marca manual de revisión: la sincronización automática del Buzón SUNAT **no está activa** y la UI lo dice.
- **Comunicación** es manual: marcar como enviado un borrador de WhatsApp no demuestra la comunicación mensual. Los borradores se muestran por cliente (`communication_drafts` no tiene `tax_period_id` y no se agregó).
- Las lecturas **nunca persisten** valores por defecto: la fila nace perezosamente en el primer cambio efectivo. Un cambio que no modifica nada no crea fila ni versión.
- Tarjetas nuevas: `buzon_pending`, `accounting_pending`, `client_communication_pending` sobre toda la población activa. Pendiente = Buzón `NOT_REVIEWED`/`REVIEWED_ACTION_REQUIRED`; Contabilidad `NOT_STARTED`/`IN_PROGRESS`/`REVIEWED` (solo `COMPLETED` y `NOT_APPLICABLE` cierran); Comunicación `NOT_STARTED`/`PREPARED`.

## Modelo de datos (migración 0019, ver ADR-0074)

`client_period_operations`: una fila por `(tenant_id, client_id, tax_period_id)` (UNIQUE); FKs compuestas a `clients(id, tenant_id)`,
`tax_periods(id, tenant_id)` y `users(id, tenant_id)` (integridad de tenant impuesta por la BD); CHECK de los tres enums,
`row_version >= 1` y `length(notes) <= 2000`. `client_period_operation_history` (append-only): quién, cuándo, campo, de → a,
`row_version_after`, con FK compuesta al alcance exacto de la operación. Reversible (0019 ↔ 0018).

## Concurrencia

Escritura con `expected_row_version` (`0` = "aún no existe fila"). Creación perezosa con `INSERT … ON CONFLICT DO NOTHING` y
actualización con `SELECT … FOR UPDATE`. Versión desactualizada ⇒ **409** `{"detail": {"code": "CLIENT_PERIOD_OPERATIONS_VERSION_CONFLICT", "current_row_version": n}}`;
la UI recarga y explica que otra persona modificó el periodo. Verificado con dos conexiones PostgreSQL reales.

## Workspace (`/cockpit/[clientId]?period=…`)

Pestañas: **Resumen**, **Apertura / Buzón**, **Documentos**, **SIRE**, **Declaraciones**, **Contabilidad**, **Pagos**, **Honorarios**
(solo con `professional_fee:read`), **Comunicación**, **Historial**. Reutiliza los paneles existentes (documentos, determinación y pagos
de impuesto, presentación, cierre y honorarios) extraídos a `DeclarationSections` / `useDeclarationWork` y `ClientFeesPanel`; la página de
detalle de la declaración y la de honorarios usan los mismos componentes. Sin permiso `cockpit:update` los estados manuales se ven en solo lectura.
La única acción de SUNAT expuesta es **"Acceso SOL supervisado"** (enlace a la página existente; solo con permisos de credencial y sesión SOL).

## Navegación

Menú agrupado (Operación / Clientes / Comunicaciones / Reportes / Configuración) con las URLs existentes intactas; sin entradas para
funciones inexistentes (no hay página de Alertas, por eso no hay ítem). En ≥768 px es una barra lateral (180 px en tablet, 220 px en
escritorio); en móvil es un cajón con botón de menú (`aria-expanded`), foco al primer enlace, cierre con Escape y al navegar. La tabla del cockpit se
desplaza dentro de su propio contenedor y la página no desborda horizontalmente.

## Rendimiento

Número fijo de consultas (periodo, clientes, declaraciones+obligación, SIRE configurado, operaciones manuales, liquidaciones, pagos,
documentos faltantes, honorarios), independiente de cuántos clientes o declaraciones haya (verificado por prueba). Las filas se derivan en
Python para toda la población activa y los filtros/paginación operan sobre ese resultado (escala prevista: decenas a pocos cientos de clientes).

## Fuera de alcance (diferido)

Sincronización del Buzón, onboarding por Ficha RUC, navegación SOL posterior al aterrizaje, accesos directos SIRE / Declaraciones y Pagos,
notificaciones SUNAT/SUNAFIL, conciliación bancaria, portal del cliente, descarga de constancias, WhatsApp Business/API, Gmail/Calendar/Drive,
CONTASIS SQL directo y DARWIN.
