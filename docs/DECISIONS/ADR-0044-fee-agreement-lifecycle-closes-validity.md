# ADR-0044: `supersede`/`cancel` de acuerdos de honorarios cierran la vigencia real

## Estado
Aceptado

## Contexto
Un acuerdo de honorarios (`professional_fee_agreements`) tiene un `EXCLUDE USING gist` que impide dos acuerdos activos con `service_type` solapado en el tiempo para el mismo cliente. Si "superar" o "cancelar" un acuerdo solo cambiara la columna `status` (dejando `valid_until` en `NULL` o sin tocar), el `EXCLUDE` — que compara la columna generada `validity_range`, no `status` — seguiría viendo el acuerdo "cancelado" como vigente para siempre, bloqueando la creación de cualquier acuerdo nuevo no solapado del mismo tipo de servicio.

## Decisión
`professional_fee_service.supersede_agreement`/`cancel_agreement` (que comparten la lógica en `_close_agreement_validity`) actualizan **ambas** columnas atómicamente: `status` (`SUPERSEDED`/`CANCELLED`) **y** `valid_until=<fecha efectiva>` (la fecha provista o "hoy" si no se especifica, validada `>= valid_from`). Esto cierra de verdad el rango de vigencia calculado por `validity_range`, liberando el espacio para que un acuerdo posterior no solapado pueda crearse sin violar el `EXCLUDE`. Ambas acciones:

- Exigen un motivo explícito (`MissingLifecycleReasonError` si falta).
- Solo se permiten sobre un acuerdo `ACTIVE` (`AgreementNotActiveError` en caso contrario).
- Escriben `professional_fee_agreement_history` con el par completo `from_status/to_status` y `from_valid_until/to_valid_until`.
- **Nunca modifican** cargos (`professional_fee_charges`) ya generados a partir del acuerdo — el cierre de vigencia es hacia adelante, no retroactivo.

Este es el mismo principio ya aplicado en `client_document_requirements` (ver ADR-0034): desactivar significa cerrar vigencia real, no solo cambiar una bandera.

## Alternativas consideradas
- **Cambiar solo `status`**: fue el diseño original detectado como defectuoso en la revisión del plan — probado explícitamente que **no** permite crear un acuerdo posterior no solapado, porque el `EXCLUDE` ignora `status`.

## Consecuencias
- `test_professional_fees.py::test_supersede_closes_valid_until_and_allows_new_agreement` prueba explícitamente que, tras `supersede`, se puede crear un acuerdo nuevo no solapado del mismo `service_type` — que fallaría con el diseño de solo `status`.
- `test_professional_fees.py::test_supersede_never_modifies_existing_charges` confirma que los cargos generados antes de superar el acuerdo no cambian.
