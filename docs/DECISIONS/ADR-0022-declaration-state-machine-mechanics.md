# ADR-0022: Mecánica de la máquina de estados de `Declaration` — transiciones no lineales, regresiones auditadas, bloqueo optimista

## Estado
Aceptado

## Contexto
El progreso operativo real de una declaración no es lineal: un revisor puede detectar que faltó un documento después de haberla marcado `DOCUMENTS_COMPLETE`, o devolver una declaración de `UNDER_REVIEW` a `IN_PROGRESS` tras encontrar un error. Estas regresiones son legítimas y deben quedar permitidas, pero **nunca silenciosas**: alguien debe explicar por qué una declaración retrocede. Además, dos usuarios pueden editar la misma declaración casi simultáneamente (cambiar su estado de flujo mientras otro registra una asignación), y un cierre reflexivo (ver ADR-0018 para los enums en sí) no debía ser alcanzable en 3.1 porque sus precondiciones dependían de tablas que todavía no existían.

## Decisión
`WORKFLOW_TRANSITIONS` (`app/services/declaration_service.py`) es una tabla explícita `dict[str, set[str]]` de transiciones permitidas — **no lineal**: por ejemplo, `DOCUMENTS_COMPLETE` puede volver a `INFORMATION_INCOMPLETE`, y `UNDER_REVIEW` puede volver a `IN_PROGRESS`. Cualquier transición fuera de esa tabla lanza `InvalidWorkflowTransitionError` (409). `FILING_TRANSITIONS` sigue el mismo patrón para la máquina de presentación (`NOT_FILED`→`READY`/`NOT_REQUIRED`, `READY`→`FILED`/`FAILED`, `FAILED`→`READY`, con `FILED`/`NOT_REQUIRED` terminales).

**Regresiones auditadas**: `WORKFLOW_REGRESSIONS` es un conjunto explícito de pares `(origen, destino)` marcados como regresión (`DOCUMENTS_COMPLETE→INFORMATION_INCOMPLETE`, `UNDER_REVIEW→IN_PROGRESS`, `READY_TO_FILE→UNDER_REVIEW`). Si la transición solicitada es una de estas, `reason` es obligatorio y no puede estar vacío (`MissingWorkflowRegressionReasonError` si falta) — una regresión sin explicación queda bloqueada por diseño, no solo por convención de UI.

**Bloqueo optimista**: cada llamada de mutación exige `row_version` explícito; `_check_row_version` compara contra el valor real antes de aplicar cualquier cambio, y cada mutación exitosa incrementa `row_version` en 1. Dos actualizaciones concurrentes sobre la misma declaración con el mismo `row_version` de partida: la segunda en llegar falla con un conflicto de versión, en vez de sobrescribir silenciosamente el trabajo de la primera.

**Cierre restringido en 3.1**: `close()` ya contenía en 3.1 la lógica completa de las 3 precondiciones (`CLOSE_ELIGIBLE_FILING`/`CLOSE_ELIGIBLE_TAX_PAYMENT`/`CLOSE_ELIGIBLE_DOCUMENT`) y el mecanismo de override (`force_close_with_pending` + `settings.allow_close_with_pending_tax`), pero era **funcionalmente inalcanzable**: `document_status` solo podía valer `NOT_CONFIGURED` (nunca `COMPLETE`, porque `declaration_document_items` no existía todavía), y el flag de override no existía como configuración real. El cierre normal quedó así deliberadamente restringido durante 3.1, sin necesidad de deshabilitarlo con un flag adicional — simplemente sus precondiciones nunca podían cumplirse hasta que Bloque 3.2 activara `tax_assessments`/`declaration_document_items`.

## Alternativas consideradas
- **Transiciones lineales estrictas (sin regresiones permitidas)**: rechazado — no refleja el flujo real de trabajo de un contador, donde detectar un problema después de avanzar es habitual, no excepcional.
- **Permitir regresiones sin motivo obligatorio**: rechazado — perdería la trazabilidad de por qué una declaración retrocedió, información necesaria para auditoría.
- **Deshabilitar `close()` explícitamente con un flag durante 3.1** en vez de dejar que sus propias precondiciones lo bloquearan naturalmente: se consideró pero no fue necesario — las precondiciones reales (documentos nunca `COMPLETE`, flag de override inexistente) ya producían el mismo efecto sin código adicional de "modo bloqueado".

## Consecuencias
- `test_declaration_state_machine.py` cubre transiciones válidas/inválidas, la exigencia de motivo en regresiones, el bloqueo optimista vía `row_version`, y que `close()` estaba restringido durante 3.1.
- Bloque 3.2 (ver `ADR-0047-operational-close-normal-vs-override.md`) activa el cierre real una vez que `document_status`/`tax_payment_status` pueden alcanzar valores terminales y `settings.allow_close_with_pending_tax` existe como configuración real.

## Compatibilidad
Sin cambios en las tablas `WORKFLOW_TRANSITIONS`/`FILING_TRANSITIONS`/`WORKFLOW_REGRESSIONS` en Bloque 3.2. La corrección administrativa de presentación (`FilingCorrectRequest`, target `NOT_FILED`/`READY`/`FILED`/`FAILED`/`NOT_REQUIRED` con `reason` obligatorio) es un mecanismo **separado** para declaraciones `FILED`, no una ampliación de `FILING_TRANSITIONS` — ver `app/routers/declarations.py::correct_filing`.

## Referencias
- Servicio: `app/services/declaration_service.py` (`WORKFLOW_TRANSITIONS`, `FILING_TRANSITIONS`, `WORKFLOW_REGRESSIONS`, `change_workflow_status`, `close`)
- Router: `app/routers/declarations.py`
- Pruebas: `backend/tests/integration/test_declaration_state_machine.py`, `backend/tests/integration/test_declaration_close_full.py`
