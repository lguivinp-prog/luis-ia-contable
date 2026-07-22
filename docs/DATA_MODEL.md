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

Acciones auditadas: `LOGIN_SUCCESS`, `LOGOUT`, `PASSWORD_CHANGED`, `SESSION_REVOKED`, `SESSION_REVOKED_ALL_OTHERS`, `CLIENT_CREATE`, `CLIENT_UPDATE`, `CLIENT_STATUS_CHANGE`, `CLIENT_OBLIGATION_ASSIGN`, `CLIENT_OBLIGATION_UPDATE`, `CLIENT_OBLIGATION_REMOVE`, `RUC_LOOKUP_QUERY`, `TAX_CATEGORY_ASSIGN`, `TAX_CATEGORY_REMOVE`, `TAX_SCHEDULE_SOURCE_CREATE`, `TAX_SCHEDULE_IMPORT_RUN_CREATE`, `TAX_SCHEDULE_IMPORT_CONFIRM`, `TAX_SCHEDULE_IMPORT_REJECT`, `TAX_SCHEDULE_SUBMIT_FOR_REVIEW`, `TAX_SCHEDULE_APPROVE`, `TAX_SCHEDULE_REJECT`, `TAX_SCHEDULE_CANCEL`, `TAX_SCHEDULE_PREVIEW_QUERY`.

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
```

## Entidades planificadas para fases futuras (no implementadas aún)

`client_contacts`, `client_tax_profiles`, `client_credentials` (bóveda real), `client_settings`, `tax_periods`, `declarations`, `declaration_status_history`, `tax_assessments`, `tax_payments`, `document_requirements`, `document_requests`, `documents`, `professional_fee_agreements`, `professional_fee_charges`, `professional_fee_payments`, `receipts`, `communications`, `communication_templates`, `notifications`, `notification_deadlines`, `cases`, `tasks`, `calendar_events`, `drive_folders`, `email_messages`, `integration_accounts`, `integration_sync_runs`, `automation_jobs`, `backup_runs`, `ai_prompt_templates`, `ai_analysis_results`, `system_settings`, `roles`, `user_roles`.

Estas tablas se diseñarán en detalle y se migrarán en los bloques de Fase 1 posteriores y en las Fases 2–6, siguiendo las mismas convenciones (`tenant_id`, sin `float` para montos, timestamps con zona horaria, historial de estados donde aplique).
