# ADR-0074: Persistencia de estados operativos manuales por cliente y periodo (Producto M1)

## Estado
Aceptado

## Contexto
El Cockpit Multi-RUC (Domain B) deriva todo de dominios existentes. Tres cosas del trabajo mensual no se pueden derivar
porque ocurren fuera del sistema: la revisión del Buzón SUNAT, el estado del trabajo contable (que vive en CONTASIS/DARWIN)
y la comunicación mensual al cliente. Hay que guardarlas sin duplicar nada derivable y sin simular integraciones inexistentes.

## Decisión
1. **Una tabla pequeña** `client_period_operations`, una fila por `(tenant, cliente, periodo)`, con solo `buzon_status`,
   `accounting_status`, `client_communication_status`, `notes`, `row_version`, `updated_by`. No se persiste ningún estado
   derivado (documentos, SIRE, declaraciones, impuesto, honorarios, vencimientos, riesgo, fase).
2. **Creación perezosa**: las lecturas devuelven valores por defecto con `row_version = 0` y nunca escriben; la fila nace en el
   primer cambio efectivo (`expected_row_version = 0`). Un cambio sin efecto no crea fila ni versión.
3. **Integridad en la BD, no solo en el servicio**: UNIQUE por alcance, FKs compuestas con `tenant_id` hacia clientes, periodos
   y usuarios, CHECK de enums, `row_version >= 1` y longitud de notas.
4. **Historial propio** `client_period_operation_history` (append-only, FK compuesta al alcance exacto) en lugar de la tabla
   genérica `audit_logs`: esa tabla está modelada por un enum de acciones y registra eventos, no la transición campo a campo
   (de → a) por cliente-periodo que el usuario necesita ver en la pestaña Historial. Es compacta (una fila por campo cambiado).
5. **Concurrencia optimista** con `row_version`; creación con `INSERT … ON CONFLICT DO NOTHING` y actualización con
   `SELECT … FOR UPDATE`; 409 con código estable `CLIENT_PERIOD_OPERATIONS_VERSION_CONFLICT`, como el resto del repositorio.
6. **Permiso propio `cockpit:update`** (admin y contador; no asistente). No se reutiliza `declaration:write` ni `task:update`:
   cambiar estos marcadores no es operar una declaración ni una tarea.
7. **Los estados manuales no participan en el riesgo** global ni se mezclan con SIRE/declaraciones: son marcadores de trabajo.

## Consecuencias
- Una tabla más y una migración reversible (0019). Sin backfill: la ausencia de fila ya significa "valores por defecto".
- Las tarjetas `*_pending` se calculan sobre la población activa con una consulta adicional fija (sin N+1).
- Los borradores de WhatsApp siguen por cliente: no se agregó `tax_period_id` a `communication_drafts`.
- Si más adelante el Buzón se sincroniza automáticamente, el campo manual puede convivir como confirmación humana.
