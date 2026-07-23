# ADR-0047: Cierre operativo normal vs. excepcional con override admin-gated

## Estado
Aceptado

## Contexto
En la subentrega 3.1, `declaration_service.close()` ya existía con su lógica completa de precondiciones, pero el cierre normal era **imposible de alcanzar** en la práctica porque `tax_assessments`/`declaration_document_items` todavía no existían — ninguna declaración podía tener `tax_payment_status`/`document_status` en un valor elegible. Bloque 3.2 no necesitó reescribir `close()` (su diseño ya era genérico); lo que faltaba era: (1) que los Dominios A/B/C existieran para que las precondiciones fueran alcanzables de verdad, y (2) gatear la vía de excepción (`force_close_with_pending`) con un control de autorización que no existía.

## Decisión
`declaration_service.close()` exige que las **tres precondiciones** estén en un valor elegible para el cierre normal: `filing_status IN ('FILED', 'NOT_REQUIRED')`, `tax_payment_status IN ('PAID', 'NO_TAX_DUE', 'CREDIT_BALANCE', 'NOT_APPLICABLE')`, `document_status IN ('COMPLETE', 'NOT_APPLICABLE')`. Si no se cumplen las tres y no se pide `force_close_with_pending`: `CloseRequiresPendingOverrideError` (409).

Para el cierre excepcional (`force_close_with_pending=true`):
- `settings.allow_close_with_pending_tax` debe ser `True` (interruptor global, `False` por defecto — ver `docs/USER_MANUAL.md`/`README` de configuración). Si está `False`: `CloseOverrideDisabledError` (422).
- Se exige `close_override_reason` explícito y no vacío (`MissingCloseOverrideReasonError` si falta).
- **Nuevo en el router** (`routers/declarations.py::close_declaration`): si `force_close_with_pending=true`, se exige además que el usuario actual tenga rol `admin` (`app.core.dependencies.ensure_admin_role`, HTTP 403 en caso contrario) — un control de una sola posición (no RBAC completo, ver deuda técnica), evaluado en el router, no en el servicio, porque depende de quién hace la llamada HTTP, no de una regla de dominio pura.
- Al cerrar con pendientes, `declarations.closed_with_pending=true` y `close_override_reason` quedan persistidos y visibles; el cierre normal deja `closed_with_pending=false` y `close_override_reason=NULL`.

## Alternativas consideradas
- **Reescribir `close()` para Bloque 3.2**: innecesario — el diseño de 3.1 ya era correcto y genérico; solo faltaba que las precondiciones fueran alcanzables (Dominios A-C) y el gateo de autorización del override (Dominio D).
- **Gatear el rol de admin dentro del servicio**: rechazado — el servicio no tiene acceso al usuario HTTP autenticado como concepto de autorización; esa responsabilidad pertenece al router.

## Consecuencias
- `test_declaration_close_full.py` prueba: cierre normal exitoso con las tres precondiciones cumplidas; cierre normal rechazado con documentos pendientes; cierre excepcional exitoso con flag habilitado + admin + motivo; y que el router rechaza con 403 a un usuario no-admin que intente `force_close_with_pending=true`.
- `ClosePanel.tsx` (frontend) solo muestra el botón de cierre excepcional a usuarios con `role === 'admin'`, y exige motivo + doble confirmación antes de enviar la solicitud.
