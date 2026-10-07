# Cockpit Multi-RUC mensual (Milestone M1, Domain B — solo lectura)

Centro de control operativo: el contador elige un periodo tributario y ve **todos los clientes activos en una sola vista**,
una fila por cliente. No sustituye a CONTASIS SQL, DARWIN IA ni SUNAT/SIRE: no reconstruye su lógica contable ni escribe en
sistemas externos. Todo lo que muestra se **deriva** de estados que ya existen; no persiste nada nuevo (sin migración).

- API: `GET /api/v1/cockpit/monthly?tax_period_id=…` (permiso `dashboard:read`; sin permisos nuevos).
- UI: `/cockpit` ("Cockpit mensual", primer ítem operativo del menú). `/dashboard/monthly` y el Tablero mensual **no cambian**
  (siguen siendo una fila por declaración).

## Granularidad: una fila por (tenant, cliente, periodo)

La población base son los **clientes activos** (`service_status = activo`), no las declaraciones. Un cliente con 621, PLAME,
SIRE-RVIE y SIRE-RCE aparece **una sola vez**; el detalle por obligación es drill-down. Un cliente activo **sin declaraciones**
en el periodo aparece con `period_generated=false`, `overall_risk=PERIOD_NOT_GENERATED` y enlace a *Generar declaraciones*
(el endpoint nunca genera declaraciones). La paginación es por **cliente**.

## Agregación

- **Declaraciones aplicables**: no canceladas y no `NOT_APPLICABLE`. `pendientes` = aplicables sin `filing_status` FILED/NOT_REQUIRED.
- **Vence** (`nearest_due_date`): mínimo `current_effective_due_date` entre las pendientes (si no hay pendientes, entre las aplicables).
- **Riesgo**: se reutiliza `declaration_risk.compute_risk_status` por declaración (única implementación). `overall_risk` es el más
  severo entre los riesgos de las declaraciones y los componentes (documentos incompletos → `PENDING_DOCUMENTS`, pago de impuesto
  pendiente → `PENDING_TAX`, cobranza vencida o que vence hoy → `PENDING_FEES`). Orden de severidad:
  `CRITICAL > OVERDUE > DUE_TODAY > PENDING_TAX > PENDING_DOCUMENTS > PERIOD_NOT_GENERATED > CLOSED_WITH_PENDING > PENDING_FEES >
  UPCOMING > ON_TRACK > CLOSED > NOT_APPLICABLE`. La cobranza de honorarios **nunca** hace parecer menos grave una obligación legal.
  Se devuelven también `declaration_worst_risk` y los estados por componente.
- **Documentos**: una sola declaración aplicable incompleta impide `COMPLETE` (peor estado gana).
- **Impuesto**: suma de liquidaciones **vigentes** y pagos **no anulados**; `tax_balance = pagable − pagado`; sin liquidación los
  montos son `null` (no 0). `tax_payment_status` toma el peor estado entre declaraciones; `NOT_DETERMINED` (ausencia de dato
  tributario, p. ej. una SIRE sin liquidación) se ignora mientras alguna declaración tenga un estado determinado.
- **Honorarios**: agregado existente por (cliente, periodo); nunca se multiplica por el número de declaraciones.

## SIRE (derivado, no persistido)

Se deriva de las declaraciones `SIRE-RVIE` y `SIRE-RCE`:

| Declaración | Estado SIRE |
|---|---|
| sin obligación SIRE asignada | `NOT_CONFIGURED` (no es "no aplica") |
| workflow `PENDING` | `NOT_STARTED` |
| `DOCUMENTS_REQUESTED`, `INFORMATION_INCOMPLETE`, `DOCUMENTS_COMPLETE`, `IN_PROGRESS`, `UNDER_REVIEW`, o filing `FAILED` | `IN_PROGRESS` |
| `READY_TO_FILE` o filing `READY` | `READY_FOR_SUNAT` |
| filing `FILED` | `PRESENTED` |
| workflow `NOT_APPLICABLE` o filing `NOT_REQUIRED` | `NOT_APPLICABLE` |

Si RVIE y RCE discrepan gana el **más pendiente** (p. ej. RVIE presentado + RCE sin iniciar ⇒ `NOT_STARTED`). Configurado pero sin
declaración vigente en un periodo generado ⇒ `NOT_STARTED`; configurado con el periodo sin generar ⇒ `null` (no derivable).

## Fase mensual (derivada, no persistida)

El trabajo de un periodo ocurre en el mes calendario siguiente (fecha de Lima, `today_lima`): día 1–4 `APERTURA`, 5–14 `OPERACION`,
≥15 `CIERRE`; antes de ese mes `PRE_APERTURA`; pasado ese mes sigue `CIERRE`. La fecha legal de vencimiento no se reemplaza: tras
vencer el riesgo pasa a `OVERDUE`/`CRITICAL` por las reglas existentes.

## Tarjetas resumen

Sobre **toda** la población activa del periodo (no dependen de los filtros): clientes activos, periodo sin generar, vencen pronto
(pendiente con vencimiento en los próximos 5 días), vencidos (`OVERDUE`/`CRITICAL`), documentos incompletos, SIRE pendiente
(`NOT_STARTED`/`IN_PROGRESS`/`READY_FOR_SUNAT`), declaraciones pendientes, pago de impuesto pendiente y críticos. No hay tarjetas de
Buzón, contabilidad ni comunicación al cliente: su estado autoritativo aún no existe (Domain C).

## Rendimiento

Número fijo de consultas (periodo, clientes, declaraciones+obligación, SIRE configurado, liquidaciones, pagos, documentos faltantes,
honorarios), independiente de cuántos clientes o declaraciones haya (verificado por prueba). Las filas se derivan en Python para toda
la población activa y los filtros/paginación operan sobre ese resultado (escala prevista: decenas a pocos cientos de clientes).
Sin caché ni vistas materializadas ni trabajos en segundo plano.

## Fuera de alcance de este dominio

Estados manuales (Buzón, contabilidad, comunicación al cliente; permiso futuro `cockpit:update`), workspace por cliente/periodo,
reestructura del menú, acciones SUNAT posteriores al aterrizaje, integración directa con CONTASIS/DARWIN.
