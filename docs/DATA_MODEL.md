# Modelo de datos — Luis IA Contable

## Convenciones globales

- Clave primaria: `id UUID` (`gen_random_uuid()`, extensión `pgcrypto`).
- Auditoría de fila: `created_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ`.
- Importes monetarios: `NUMERIC(12,2)`, nunca `float`.
- Fechas: `DATE` para fechas calendario, `TIMESTAMPTZ` para instantes; la zona horaria de presentación (`America/Lima`) se aplica en la capa de aplicación.
- Toda tabla de negocio incluye `tenant_id UUID NOT NULL REFERENCES tenants(id)`.
- Sin eliminación física de datos de negocio (contribuyentes, obligaciones asignadas): se usa estado (`service_status`, `is_active`, etc.).

## Tablas implementadas en esta entrega

### `tenants`
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| name | VARCHAR(120) NOT NULL | |
| created_at | TIMESTAMPTZ | |

### `users`
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NOT NULL | |
| email | VARCHAR(255) UNIQUE NOT NULL | |
| full_name | VARCHAR(200) NOT NULL | |
| password_hash | VARCHAR(255) NOT NULL | Argon2id encoded string |
| role | VARCHAR(30) NOT NULL DEFAULT 'admin' | preparado para roles futuros |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| last_login_at | TIMESTAMPTZ NULL | |
| created_at, updated_at | TIMESTAMPTZ | |

### `sessions`
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NOT NULL | |
| user_id | UUID FK users NOT NULL | |
| token_hash | VARCHAR(64) UNIQUE NOT NULL | SHA-256 hex del token; el token crudo solo existe en la cookie del navegador |
| created_at | TIMESTAMPTZ NOT NULL | |
| last_seen_at | TIMESTAMPTZ NOT NULL | |
| expires_at | TIMESTAMPTZ NOT NULL | |
| revoked_at | TIMESTAMPTZ NULL | |
| ip_address | VARCHAR(45) NULL | |
| user_agent | VARCHAR(255) NULL | |

### `audit_logs`
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NOT NULL | |
| user_id | UUID FK users NULL | |
| action | VARCHAR(60) NOT NULL | validado por Enum de Python — ver lista abajo |
| entity_type | VARCHAR(60) NULL | |
| entity_id | UUID NULL | |
| metadata | JSONB NULL | nunca contiene secretos |
| ip_address | VARCHAR(45) NULL | |
| user_agent | VARCHAR(255) NULL | |
| created_at | TIMESTAMPTZ NOT NULL | |

Acciones auditadas: `LOGIN_SUCCESS`, `LOGOUT`, `PASSWORD_CHANGED`, `SESSION_REVOKED`, `SESSION_REVOKED_ALL_OTHERS`, `CLIENT_CREATE`, `CLIENT_UPDATE`, `CLIENT_STATUS_CHANGE`, `CLIENT_OBLIGATION_ASSIGN`, `CLIENT_OBLIGATION_UPDATE`, `CLIENT_OBLIGATION_REMOVE`, `RUC_LOOKUP_QUERY`, `TAX_CATEGORY_ASSIGN`, `TAX_CATEGORY_REMOVE`, `TAX_SCHEDULE_SOURCE_CREATE`, `TAX_SCHEDULE_IMPORT_RUN_CREATE`, `TAX_SCHEDULE_IMPORT_CONFIRM`, `TAX_SCHEDULE_IMPORT_REJECT`, `TAX_SCHEDULE_SUBMIT_FOR_REVIEW`, `TAX_SCHEDULE_APPROVE`, `TAX_SCHEDULE_REJECT`, `TAX_SCHEDULE_CANCEL`, `TAX_SCHEDULE_PREVIEW_QUERY`, `TAX_PERIOD_CREATE`, `TAX_PERIOD_CLOSE`, `TAX_PERIOD_REOPEN`, `DECLARATION_CREATE`, `DECLARATION_WORKFLOW_CHANGE`, `DECLARATION_ASSIGN`, `DECLARATION_FILE`, `DECLARATION_FILING_CORRECT`, `DECLARATION_RECALCULATE`, `DECLARATION_REOPEN`, `DECLARATION_CLOSE`, `DECLARATION_CANCEL`, `DECLARATION_GENERATION_PREVIEW`, `DECLARATION_GENERATION_ITEM_EXCLUDE`, `DECLARATION_GENERATION_CONFIRM`, `DECLARATION_GENERATION_REFRESH_PREVIEW`, `DECLARATION_GENERATION_CANCEL` (Bloque 3.1), y desde Bloque 3.2: `DOCUMENT_CATALOG_CREATE`, `DOCUMENT_REQUIREMENT_ASSIGN`, `DOCUMENT_ITEM_STATUS_CHANGE`, `DOCUMENT_REQUIREMENTS_SYNC`, `TAX_ASSESSMENT_CREATE`, `TAX_ASSESSMENT_CORRECT`, `TAX_PAYMENT_CREATE`, `TAX_PAYMENT_VOID`, `TAX_PAYMENT_OVERPAYMENT_CONFIRM`, `FEE_AGREEMENT_CREATE`, `FEE_AGREEMENT_SUPERSEDE`, `FEE_AGREEMENT_CANCEL`, `FEE_CHARGE_CREATE`, `FEE_PAYMENT_CREATE`, `FEE_PAYMENT_VOID`, `FEE_RECEIPT_ISSUE`, `FEE_RECEIPT_VOID`, `DECLARATION_CLOSE_WITH_OVERRIDE`, `REPORT_EXPORT`.

**Nota importante (ver ADR-0009):** ningún fallo de login (exista o no la cuenta) escribe en `audit_logs` — esa información vive exclusivamente en `login_attempts` y `security_events`, para no filtrar por auditoría lo que la mitigación de enumeración por tiempo oculta en la respuesta HTTP.

### `login_attempts` (ADR-0009)
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NULL | **solo se rellena en el intento exitoso** |
| identifier | VARCHAR(255) NOT NULL | email normalizado (NFC + minúsculas + `strip`) |
| success | BOOLEAN NOT NULL | |
| attempted_at | TIMESTAMPTZ NOT NULL | |
| ip_address | VARCHAR(45) NULL | |
| user_agent | VARCHAR(255) NULL | |

Nunca se borran filas: el conteo de bloqueo es "fallos consecutivos desde el último `success=true`".

### `security_events` (ADR-0009 / ADR-0010)
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NULL | interno, nunca expuesto en la respuesta HTTP |
| user_id | UUID FK users NULL | idem |
| event_type | VARCHAR(60) NOT NULL | `LOGIN_FAILURE_ATTEMPT`, `LOGIN_ATTEMPT_WHILE_LOCKED`, `LOGIN_LOCKOUT_TRIGGERED`, `LOGIN_UNLOCKED_MANUALLY`, `SESSION_EXPIRED_INACTIVITY`, `SESSION_EXPIRED_ABSOLUTE`, `SESSION_REVOKED_CONCURRENT_LIMIT`, `SESSION_REVOKED_PASSWORD_CHANGE`, `CSRF_REJECTED`, `CONCURRENT_APPROVAL_CONFLICT` |
| severity | VARCHAR(20) NOT NULL | `info`, `warning`, `critical` |
| metadata | JSONB NULL | |
| ip_address, user_agent | VARCHAR NULL | |
| created_at | TIMESTAMPTZ NOT NULL | |

### `sessions` — columna añadida (ADR-0010)
| Columna | Tipo | Notas |
|---|---|---|
| csrf_token_hash | VARCHAR(64) UNIQUE NOT NULL | hash del token CSRF double-submit; rota en login, se borra en logout |

### `clients` (contribuyentes)
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NOT NULL | |
| ruc | CHAR(11) NOT NULL | `UNIQUE (tenant_id, ruc)` |
| ruc_last_digit | SMALLINT GENERATED ALWAYS AS (...) STORED | último dígito del RUC, para cronogramas futuros |
| business_name | VARCHAR(255) NOT NULL | razón social |
| trade_name | VARCHAR(255) NULL | nombre comercial |
| person_type | VARCHAR(20) NOT NULL | `natural` \| `juridica` |
| taxpayer_type | VARCHAR(100) NULL | tipo de contribuyente SUNAT (texto libre en esta fase) |
| ruc_status | VARCHAR(30) NOT NULL | `ACTIVO`, `BAJA_PROVISIONAL`, `BAJA_DEFINITIVA`, `BAJA_DE_OFICIO`, `SUSPENSION_TEMPORAL` |
| fiscal_address_condition | VARCHAR(30) NOT NULL | `HABIDO`, `NO_HABIDO`, `NO_HALLADO`, `PENDIENTE` |
| fiscal_address | TEXT NULL | |
| ubigeo_code | CHAR(6) NULL | |
| department, province, district | VARCHAR(60) NULL | |
| main_economic_activity | VARCHAR(255) NULL | |
| secondary_economic_activities | JSONB NULL | array de strings |
| tax_regime | VARCHAR(20) NOT NULL | `NRUS`, `RER`, `RMT`, `GENERAL` |
| activities_start_date | DATE NULL | |
| issuance_system | VARCHAR(20) NULL | `manual`, `electronico`, `mixto` |
| phone, email | VARCHAR | |
| legal_representative_name, legal_representative_doc | VARCHAR | |
| contact_person | VARCHAR(255) NULL | |
| whatsapp_number | VARCHAR(30) NULL | |
| is_good_taxpayer | BOOLEAN NOT NULL DEFAULT false | **columna heredada del Bloque 1, congelada (ver ADR-0013): ya no se lee ni se escribe.** `ClientOut.is_good_taxpayer` se calcula dinámicamente desde `client_tax_categories` |
| monthly_fee | NUMERIC(10,2) NULL | honorario mensual |
| payment_day | SMALLINT NULL | 1–31, CHECK constraint |
| onboarding_date | DATE NULL | fecha de incorporación al estudio |
| service_status | VARCHAR(20) NOT NULL DEFAULT 'prospecto' | `prospecto`, `activo`, `suspendido`, `pendiente_cierre`, `inactivo` |
| notes | TEXT NULL | |
| tags | JSONB NULL | array de strings |
| last_verified_at | TIMESTAMPTZ NULL | |
| sol_username_encrypted, sol_password_encrypted | BYTEA NULL | **preparadas, no funcionales** — ver [SECURITY_MODEL.md](SECURITY_MODEL.md) |
| sol_cipher_version | SMALLINT NULL | |
| created_at, updated_at, created_by, updated_by | | |

### `obligation_catalog`
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| code | VARCHAR(30) UNIQUE NOT NULL | ej. `621`, `PLAME`, `SIRE-RVIE` |
| name | VARCHAR(150) NOT NULL | |
| description | TEXT NULL | |
| periodicity | VARCHAR(20) NOT NULL | `mensual`, `anual`, `eventual` |
| applies_to_regime | JSONB NULL | array de regímenes aplicables |
| source | TEXT NOT NULL | referencia normativa |
| source_version | VARCHAR(50) NOT NULL | fecha/versión del dato — nunca cronogramas inventados |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| created_at, updated_at | | |

### `client_obligations`
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NOT NULL | |
| client_id | UUID FK clients NOT NULL | |
| obligation_id | UUID FK obligation_catalog NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| assigned_at | TIMESTAMPTZ NOT NULL | |
| deactivated_at | TIMESTAMPTZ NULL | |
| notes | TEXT NULL | |
| created_at, updated_at | | |
| | | `UNIQUE (client_id, obligation_id)` |

### `tax_category_catalog` (ADR-0013)
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| code | VARCHAR(50) UNIQUE NOT NULL | ej. `BUEN_CONTRIBUYENTE`, `ENTIDAD_PUBLICA` |
| name | VARCHAR(150) NOT NULL | |
| description | TEXT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |

### `client_tax_categories` (ADR-0013)
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NOT NULL | |
| client_id | UUID FK clients NOT NULL | |
| category_code | VARCHAR(50) FK tax_category_catalog NOT NULL | |
| valid_from | DATE NOT NULL | |
| valid_until | DATE NULL | `NULL` = vigente indefinidamente |
| validity_range | DATERANGE GENERATED ALWAYS AS (`daterange(valid_from, valid_until, '[]')`) STORED | solo para la restricción de exclusión |
| notes | TEXT NULL | |
| | | `EXCLUDE USING gist (client_id WITH =, category_code WITH =, validity_range WITH &&)` — requiere `btree_gist` |

### `tax_schedule_sources` (ADR-0011)
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK tenants NOT NULL | |
| obligation_id | UUID FK obligation_catalog NOT NULL | una fuente por obligación — nunca se asume que todas comparten cronograma |
| name, description, reference_url | | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |

### `tax_schedule_versions` (ADR-0011 / ADR-0015)
| Columna | Tipo | Notas |
|---|---|---|
| id | UUID PK | |
| tenant_id, source_id | FK | |
| version_label | VARCHAR(50) NOT NULL | |
| fiscal_year | SMALLINT NOT NULL | |
| schedule_type | VARCHAR(30) NOT NULL DEFAULT 'GENERAL' | |
| scope_category_code | VARCHAR(50) FK tax_category_catalog NULL | `NULL` = alcance general |
| status | VARCHAR(20) NOT NULL DEFAULT 'DRAFT' | `DRAFT`/`IMPORTED`/`PENDING_REVIEW`/`APPROVED`/`SUPERSEDED`/`REJECTED`/`CANCELLED` |
| normative_reference, source_document_url, checksum, observations | | |
| import_run_id | UUID FK tax_schedule_import_runs NULL | `use_alter` para romper el ciclo de creación con `import_runs` |
| superseded_by_version_id | UUID FK tax_schedule_versions NULL | |
| created_by, approved_by, approved_at | | |
| | | `ux_tax_schedule_versions_one_approved_per_scope`: índice único parcial `WHERE status='APPROVED'` sobre `(tenant_id, source_id, fiscal_year, schedule_type, COALESCE(scope_category_code,''))` |

### `tax_schedule_entries` / `tax_schedule_exceptions` (ADR-0011 / ADR-0014 / ADR-0015)
| Columna | Tipo | Notas |
|---|---|---|
| id, tenant_id, version_id | | |
| tax_period | VARCHAR(7) NOT NULL | `YYYY` o `YYYY-MM` |
| applicable_last_digits | SMALLINT[] NULL | `NULL` = todos; `CHECK (... <@ ARRAY[0..9]::smallint[])` |
| required_category_code | VARCHAR(50) FK tax_category_catalog NULL | |
| priority | SMALLINT NULL | solo desempate final, nunca inventado |
| due_date_original, due_date_effective | DATE NOT NULL | (`tax_schedule_entries`) |
| due_date_effective | DATE NOT NULL | (`tax_schedule_exceptions`, sin fecha "original") |
| due_date_type | VARCHAR(30) NOT NULL | (`tax_schedule_entries`) |
| reason | TEXT NOT NULL | solo `tax_schedule_exceptions`; las excepciones siempre tienen prioridad sobre las entradas generales |
| notes | TEXT NULL | |

### `tax_schedule_approval_history` (ADR-0011)
| Columna | Tipo | Notas |
|---|---|---|
| id, tenant_id, version_id | | |
| from_status | VARCHAR(20) NULL | |
| to_status | VARCHAR(20) NOT NULL | |
| actor_user_id | UUID FK users NULL | |
| comment | TEXT NULL | obligatorio en `approve`/`reject`/`cancel` a nivel de servicio |
| created_at | TIMESTAMPTZ NOT NULL | |

### `tax_schedule_import_runs` (ADR-0012)
| Columna | Tipo | Notas |
|---|---|---|
| id, tenant_id, source_id | | |
| version_id | UUID FK tax_schedule_versions NULL | se rellena al confirmar |
| original_filename, file_checksum | | |
| raw_content | TEXT NOT NULL | CSV crudo, conservado como evidencia (ver ADR-0012) |
| fiscal_year, schedule_type, scope_category_code | | alcance declarado al subir el archivo |
| parsed_rows | JSONB NULL | filas parseadas, antes de confirmarse como `tax_schedule_entries` |
| row_count | INTEGER NULL | |
| status | VARCHAR(20) NOT NULL DEFAULT 'RECEIVED' | `RECEIVED`/`VALIDATED`/`REJECTED`/`CONFIRMED` |
| validation_errors, diff_summary | JSONB NULL | errores estructurales y diferencias vs. la versión `APPROVED` previa del mismo scope |
| created_at, created_by | | |

## Bloque 3.1 — Ciclo operativo (periodos, declaraciones, generación masiva)

### `tax_periods`
| Columna | Tipo | Notas |
|---|---|---|
| id, tenant_id | | |
| period_type | VARCHAR(20) NOT NULL | `MONTHLY`, `ANNUAL`, `SPECIAL` |
| fiscal_year | SMALLINT NOT NULL | |
| month | SMALLINT NULL | `NULL` para `ANNUAL`/`SPECIAL` |
| canonical_code | VARCHAR(20) NOT NULL | `UNIQUE(tenant_id, canonical_code)` |
| schedule_tax_period_code | VARCHAR(7) NULL | vínculo hacia `tax_schedule_entries.tax_period` |
| display_name, description | | |
| start_date, end_date | DATE NOT NULL | |
| is_open | BOOLEAN NOT NULL DEFAULT true | cerrar bloquea toda mutación de declaraciones del periodo |
| created_at, updated_at | | |

`tax_period_status_history`: `from_is_open`/`to_is_open`, `actor_user_id`, `reason`, `created_at`.

### `declarations`
| Columna | Tipo | Notas |
|---|---|---|
| id, tenant_id | | |
| client_id | UUID | FK compuesta `(client_id, tenant_id) → clients(id, tenant_id)` |
| client_obligation_id | UUID | FK compuesta `(client_obligation_id, tenant_id, client_id) → client_obligations(id, tenant_id, client_id)` |
| tax_period_id | UUID | FK compuesta `(tax_period_id, tenant_id) → tax_periods(id, tenant_id)` |
| assigned_user_id | UUID NULL | |
| workflow_status | VARCHAR(30) | `PENDING`→`DOCUMENTS_REQUESTED`→…→`READY_TO_FILE`→`CLOSED`, más `NOT_APPLICABLE`/`CANCELLED` terminales |
| filing_status | VARCHAR(20) | `NOT_FILED`/`READY`/`FILED`/`FAILED`/`NOT_REQUIRED` |
| tax_payment_status | VARCHAR(20) | `NOT_DETERMINED`/`NO_TAX_DUE`/`CREDIT_BALANCE`/`PAYMENT_PENDING`/`PARTIALLY_PAID`/`PAID`/`NOT_APPLICABLE` (`PAYMENT_PENDING` agregado en 3.2, ver ADR-0038) |
| document_status | VARCHAR(20) | `NOT_CONFIGURED`/`PENDING`/`PARTIAL`/`OBSERVED`/`COMPLETE`/`NOT_APPLICABLE` |
| initial_effective_due_date | DATE NOT NULL | nunca cambia tras la creación |
| current_original_due_date, current_effective_due_date | DATE NOT NULL | se actualizan en cada recálculo |
| schedule_source_id, schedule_version_id, schedule_rule_type, schedule_entry_id, schedule_exception_id | | snapshot de la regla de cronograma resuelta |
| schedule_snapshot | JSONB NOT NULL | copia versionada (`schedule_snapshot_schema_version`) de la resolución completa |
| presentation_date, order_number, filing_reference | | se completan al presentar |
| closed_at, closed_by, closed_with_pending, close_override_reason | | ver ADR-0047 |
| cancelled_at, cancellation_reason | | |
| row_version | INTEGER NOT NULL DEFAULT 1 | bloqueo optimista para transiciones de ciclo de vida general |
| | | `UNIQUE(id, tenant_id)`, `UNIQUE(id, tenant_id, client_id)` (ver ADR-0036), `UNIQUE(tenant_id, client_id, client_obligation_id, tax_period_id)` |

5 tablas de historial (`declaration_status_history`, `declaration_due_date_history`, `declaration_assignment_history`, `filing_history`, `declaration_reopen_history`) registran cada transición con `actor_user_id`/`reason`/`origin`/`audit_correlation_id`; la creación de una declaración escribe una fila de nacimiento en las 4 primeras en una sola transacción.

### `declaration_generation_runs` / `declaration_generation_run_items`
Ejecución masiva de generación por periodo: `status` (`PENDING`/`PREVIEWED`/`STALE`/`CANCELLED`/`COMPLETED`/`COMPLETED_WITH_ERRORS`/`FAILED`), `preview_checksum` (detecta cambios de datos entre la previsualización y la confirmación → `STALE`), `stale_diff`/`validation_errors` JSONB. Cada ítem clasifica el resultado por cliente/obligación (`APTO`, `YA_EXISTENTE`, `CLIENTE_INACTIVO`, `OBLIGACION_INACTIVA`, `SIN_CRONOGRAMA`, `AMBIGUO`, `SIN_REGLA_APLICABLE`, `ERROR_INESPERADO`) y se confirma dentro de un `SAVEPOINT` propio (un fallo en un ítem no revierte el resto del lote).

## Bloque 3.2 — Control documental, impuestos, honorarios, reportes

### Control documental (ADR-0033 a ADR-0036)

**`document_requirement_catalog`**: catálogo híbrido sistema/tenant (`is_system`, `tenant_id NULL` para filas de sistema, FK real hacia `tenants`). Ver ADR-0033.

**`client_document_requirements`**: asignación de un requisito a un cliente, con vigencia (`valid_from`/`valid_until`/`validity_range` generada), `client_obligation_id NULL` = requisito general o FK compuesta hacia la obligación asignada concreta (ADR-0034), `EXCLUDE USING gist` que impide solapamientos del mismo requisito/obligación/periodicidad para el mismo cliente.

**`declaration_document_items`**: materialización real por declaración. `client_id` propio + dos FKs compuestas idénticas en esa columna hacia `declarations` y `client_document_requirements` (ADR-0036, impide cruces entre clientes). `status` (7 valores: `NOT_REQUESTED`/`REQUESTED`/`PARTIAL`/`RECEIVED`/`OBSERVED`/`ACCEPTED`/`NOT_APPLICABLE`), `file_metadata JSONB` validado (`DocumentFileMetadataV1`, `extra=forbid`, nunca una ruta/URL real). `UNIQUE(tenant_id, declaration_id, client_document_requirement_id)` sostiene la idempotencia de la sincronización (ADR-0035).

**`document_item_status_history`**: historial de cambios de estado de cada ítem.

### Determinación y pagos tributarios (ADR-0038 a ADR-0043)

**`tax_assessments`**: `assessment_type` (`ORIGINAL`/`CORRECTION`), `sequence_number`, `gross_tax_amount`/`credits_applied`/`total_payable`/`remaining_credit` (`NUMERIC(14,2)`, fórmula fija por `CHECK`), `is_current` (único parcial por declaración). Ver ADR-0038.

**`tax_assessment_history`**: par `previous_assessment_snapshot`/`new_assessment_snapshot` (JSONB, nunca vacíos).

**`tax_payments`**: `declaration_id`, `assessment_id` (referencia histórica, nunca usada para calcular saldo — ADR-0040), `amount`, `payment_method`, campos de sobrepago (`overpayment_confirmed`/`overpayment_reason`/`overpayment_amount`/`confirmed_by`/`confirmed_at`, ADR-0043) y de anulación (`is_voided`/`voided_at`/`voided_by`/`void_reason`), `created_by`, `idempotency_key UUID NOT NULL` + `UNIQUE(tenant_id, idempotency_key)` (ADR-0041).

**`tax_payment_history`**: nacimiento (`NULL→ACTIVE`) y anulación (`ACTIVE→VOIDED`) de cada pago.

### Honorarios profesionales (ADR-0044 a ADR-0046)

**`professional_fee_agreements`**: `service_type`, `amount`, `status` (`ACTIVE`/`SUPERSEDED`/`CANCELLED`), vigencia real (`valid_from`/`valid_until`/`validity_range`), `EXCLUDE USING gist` por `(tenant_id, client_id, service_type, validity_range)`. `supersede`/`cancel` cierran `valid_until` de verdad (ADR-0044), nunca solo cambian `status`.

**`professional_fee_agreement_history`**: par `from_status/to_status` + `from_valid_until/to_valid_until`.

**`professional_fee_charges`**: `agreement_id`, `tax_period_id`, `amount`, `status` (6 valores, sin `OVERDUE` persistido — ADR-0045), `due_date`, `issued_at`. `UNIQUE(tenant_id, agreement_id, tax_period_id)` (idempotencia de generación de cargos).

**`professional_fee_charge_status_history`**: ciclo de vida del cargo como documento.

**`professional_fee_payments`**: mismo patrón que `tax_payments` mutatis mutandis (sobrepago, anulación, `idempotency_key`), pero bloquea `professional_fee_charges` `FOR UPDATE`, **nunca** `declarations` (ADR-0042).

**`professional_fee_payment_history`**: nacimiento/anulación de cada pago (tabla separada de `professional_fee_charge_status_history` — ADR-0045).

**`professional_receipts`**: `charge_id`, `issued_by` (FK compuesta hacia `users`), `receipt_type`/`number` (`UNIQUE(tenant_id, receipt_type, number)`), `status` (`ISSUED`/`VOIDED`) con `CHECK` de coherencia; la suma de recibos `ISSUED` de un cargo nunca puede exceder su `amount` (ADR-0046, validado en servicio).

## Diagrama de relaciones (esta entrega)

```
tenants 1──n users 1──n sessions
tenants 1──n clients 1──n client_obligations n──1 obligation_catalog
tenants 1──n clients 1──n client_tax_categories n──1 tax_category_catalog
tenants 1──n audit_logs (n──1 users, opcional)
tenants 1──n login_attempts, security_events
tenants 1──n tax_schedule_sources 1──n tax_schedule_versions 1──n tax_schedule_entries/exceptions
tax_schedule_versions 1──n tax_schedule_approval_history
tax_schedule_sources 1──n tax_schedule_import_runs 1──1 tax_schedule_versions (al confirmarse)

tenants 1──n tax_periods
clients 1──n declarations n──1 client_obligations, n──1 tax_periods
declarations 1──n {declaration_status_history, declaration_due_date_history,
                   declaration_assignment_history, filing_history, declaration_reopen_history}
tax_periods 1──n declaration_generation_runs 1──n declaration_generation_run_items

document_requirement_catalog 1──n client_document_requirements n──1 client_obligations (opcional)
declarations 1──n declaration_document_items n──1 client_document_requirements (mismo client_id en ambas FKs)
declaration_document_items 1──n document_item_status_history

declarations 1──n tax_assessments 1──n tax_assessment_history
declarations 1──n tax_payments n──1 tax_assessments (referencia historica)
tax_payments 1──n tax_payment_history

clients 1──n professional_fee_agreements 1──n professional_fee_agreement_history
professional_fee_agreements 1──n professional_fee_charges 1──n professional_fee_charge_status_history
professional_fee_charges 1──n professional_fee_payments 1──n professional_fee_payment_history
professional_fee_charges 1──n professional_receipts
```

## Entidades planificadas para fases futuras (no implementadas aún)

`client_contacts`, `client_tax_profiles`, `client_credentials` (bóveda real), `client_settings`, `communications`, `communication_templates`, `notifications`, `notification_deadlines`, `cases`, `tasks`, `calendar_events`, `drive_folders`, `email_messages`, `integration_accounts`, `integration_sync_runs`, `automation_jobs`, `backup_runs`, `ai_prompt_templates`, `ai_analysis_results`, `system_settings`, `roles`, `user_roles` (RBAC real — ver deuda técnica).

Estas tablas se diseñarán en detalle y se migrarán en los bloques de Fase 1 posteriores y en las Fases 2–6, siguiendo las mismas convenciones (`tenant_id`, sin `float` para montos, timestamps con zona horaria, historial de estados donde aplique).
