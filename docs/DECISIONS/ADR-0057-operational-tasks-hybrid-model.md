# ADR-0057: Gestión operativa de tareas — modelo híbrido materializado + riesgo calculado

## Estado
Aceptado

## Contexto
Ningún concepto de `task`/`reminder`/`alert`/`agenda`/prioridad-de-tarea/`assignee` genérico existe antes de
este bloque. Lo existente es específico por entidad (`Declaration.assigned_user_id`,
`ClientDocumentRequirement.responsible_user_id`, `reminder_lead_days`/`reminder_count` sin consumidor). El
riesgo/vencido siempre se calcula en vivo, nunca se persiste — precedente explícito en
`professional_fee.py:35-39` (`ProfessionalFeeChargeStatus` excluye `OVERDUE` deliberadamente: depender del
paso del tiempo sin un job programado dejaría la columna desactualizada sin un scheduler 24/7, que este
repositorio no tiene ni introduce aquí).

## Decisión

### Modelo híbrido: tareas materializadas + eventos normativos calculados
`operational_tasks` son filas reales con ciclo de vida propio (estado, responsable, comentarios, historial),
pero `OVERDUE` nunca es una columna: siempre `due_at < clock.utcnow() AND status NOT IN ('COMPLETED',
'CANCELLED')` (ver ADR-0060). La generación automática (ADR-0059) materializa filas nuevas basándose en el
estado *actual* de declaraciones/documentos/honorarios y resuelve automáticamente una tarea generada cuando su
fuente deja de requerir trabajo — la única mutación automática permitida sobre una tarea ya creada.

### 5 formas estructurales de FK compuesta en vez de `source_type`/`source_id`
`operational_tasks` nunca usa una FK débil polimórfica. En su lugar, `ck_operational_tasks_source_shape`
define 5 formas mutuamente excluyentes (sin ancla, declaración sola, ítem+declaración, honorario,
pago+declaración), cada una con FK compuestas reales hacia las tablas correspondientes. Esto hace imposible a
nivel de base de datos que una tarea referencie una combinación incoherente (p. ej. un ítem documental de una
declaración distinta a la referenciada).

### Cadena de 2 FK verificada para honorarios (`charge → agreement → client`)
`professional_fee_charges` no lleva `client_id` propio — la relación real es `charge → agreement → client`.
En vez of confiar en un `client_id` de aplicación sin verificar, `operational_tasks` añade
`professional_fee_agreement_id` con 2 FK compuestas: `fk_operational_tasks_fee_charge_tenant_agreement`
(verifica que el cargo pertenece al acuerdo referenciado) y `fk_operational_tasks_fee_agreement_tenant_client`
(verifica que el acuerdo pertenece al cliente referenciado). Together garantizan que `client_id` de una tarea
`FEE_COLLECTION` es *provably* correcto — nunca solo confiado por la aplicación, tanto para tareas generadas
como para tareas manuales que opcionalmente se vinculan a un cliente (ver ADR-0061 sobre la precisión de
`client_id` opcional en creación manual).

### Por qué `tax_period_id` no se incluyó en este bloque
Ninguna de las 3 reglas de generación automática (ADR-0059) lo necesita: toda tarea vinculada a una
declaración identifica su periodo transitivamente vía `declaration_id → declarations.tax_period_id`. Si un
bloque futuro necesita tareas ancladas solo a un periodo sin declaración, se añadirá entonces con su propia FK
compuesta verificable — no se añade especulativamente aquí.

### Por qué no existe una tabla `internal_alerts`
Las 6 categorías de alerta derivada (`overdue_tasks`, `due_today`, `due_next_7_days`, `urgent_tasks`,
`unassigned_tasks`, `source_due_mismatch`) son 100% derivables de `operational_tasks` en tiempo de consulta
(ver ADR-0060). Persistirlas crearía una segunda fuente de verdad para el mismo hecho, con el mismo riesgo de
desincronización que llevó a nunca persistir `OVERDUE`.

## Alternativas consideradas
- **FK polimórfica `source_type`/`source_id`**: rechazada — pierde toda verificación de integridad referencial
  real a nivel de base de datos; el patrón establecido en todo el repositorio (13 migraciones previas) es FK
  compuesta explícita.
- **`client_id` de honorarios confiado sin cadena de verificación**: rechazado tras detectar que
  `professional_fee_charges` no lleva `client_id` propio — confiar en un valor de aplicación sin la cadena de
  2 FK permitiría persistir una tarea con un cliente incorrecto sin que la base de datos lo detectara.
- **Tabla `internal_alerts` persistida**: rechazada por el mismo principio que ya excluye `OVERDUE`
  persistido — ver Contexto.

## Consecuencias
- 4 tablas nuevas (`operational_tasks`, `task_change_history`, `task_comments`, `task_generation_runs`), 11 FK
  compuestas + 7 `CHECK` en `operational_tasks`, 4 constraints auxiliares no destructivas en tablas
  preexistentes, 2 `UNIQUE(id, tenant_id)` internas — migración `0014`, única, sin backfill (tablas vacías).
- Toda consulta de "tareas vencidas" en cualquier capa (API, agenda, alertas, dashboard, reportes) debe
  calcular la condición en el momento de la consulta — nunca leer una columna `OVERDUE`.
