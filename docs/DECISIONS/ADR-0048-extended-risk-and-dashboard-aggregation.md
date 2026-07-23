# ADR-0048: Riesgo extendido de declaración y tablero agregado sin duplicación de filas

## Estado
Aceptado

## Contexto
`declaration_risk.compute_risk_status` (3.1) solo consideraba `workflow_status`/`filing_status`/fecha de vencimiento — razonable en 3.1 porque `tax_payment_status`/`document_status` nunca salían de sus valores iniciales. Con los Dominios A-C, esos dos campos ahora reflejan realidad operativa (impuesto determinado/pagado, documentos completos u observados), y el tablero necesita mostrar honorarios y datos tributarios agregados sin que un cliente con varios documentos/pagos/cargos en el mismo periodo produzca más de una fila por declaración.

## Decisión
**Riesgo extendido**: `compute_risk_status` gana tres parámetros con defaults retrocompatibles (`document_status`, `tax_payment_status`, `closed_with_pending`) y tres valores nuevos:
- `CLOSED_WITH_PENDING`: la declaración está `CLOSED` y `closed_with_pending=true`.
- `PENDING_TAX`: `filing_status` ya es elegible (`FILED`/`NOT_REQUIRED`) pero `tax_payment_status` todavía no es elegible.
- `PENDING_DOCUMENTS`: `filing_status` elegible y `tax_payment_status` elegible, pero `document_status` todavía no.

Solo cuando las tres dimensiones son elegibles se llega a `ON_TRACK` real (antes, con `filing_status` elegible ya bastaba). `risk_status_sql_expression` tiene la expresión SQL equivalente; `test_declaration_risk.py` prueba paridad Python/SQL para los 3 valores nuevos además de los ya existentes.

**Tablero sin duplicación**: `dashboard_service.build_tax_aggregates`/`build_fee_aggregates` (`app/services/dashboard_service.py`) calculan, con consultas agregadas adicionales (nunca N+1, nunca un `JOIN` que multiplique filas de `declarations`):
- Agregado tributario **1:1 por `declaration_id`** (determinación vigente + suma de pagos válidos + conteo de documentos faltantes) — no hay riesgo de duplicación porque `tax_assessments`/`tax_payments`/`declaration_document_items` se agregan (o filtran a `is_current`) antes de fusionarse con cada declaración.
- Agregado de honorarios **por `(client_id, tax_period_id)`** — un cargo de honorarios no está atado 1:1 a una declaración (dos declaraciones del mismo cliente/periodo con obligaciones distintas comparten el mismo agregado, que se **repite**, nunca se **multiplica**, en cada una).

El router (`routers/dashboard.py`) sigue devolviendo exactamente una fila por declaración; los aggregates se calculan aparte y se fusionan en Python por clave (`declaration_id` o `(client_id, tax_period_id)`).

## Alternativas consideradas
- **Un `JOIN` directo del tablero hacia `declaration_document_items`/`tax_payments`/`professional_fee_charges`**: rechazado explícitamente — multiplicaría filas de declaraciones por cada fila hija, requiriendo `DISTINCT`/agregación SQL compleja propensa a errores de conteo.

## Consecuencias
- `test_dashboard.py::test_dashboard_single_row_per_declaration_with_correct_fee_aggregate` prueba, con un cliente con 2 documentos + 2 pagos parciales + 2 cargos de honorarios compartidos entre 2 declaraciones del mismo periodo, que el tablero devuelve exactamente 2 filas (una por declaración) con los importes agregados correctos.
- El frontend (`dashboard/page.tsx`) expone filtros por `tax_payment_status`, `document_status` y `closed_with_pending`, y columnas para documentos faltantes, impuesto determinado/pagado, y honorarios (cargos/importe/pagado/saldo/peor riesgo).
