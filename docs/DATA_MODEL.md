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
| user_id | UUID FK users NULL | nulo en intentos de login fallidos antes de identificar usuario |
| action | VARCHAR(60) NOT NULL | validado por Enum de Python — ver lista abajo |
| entity_type | VARCHAR(60) NULL | |
| entity_id | UUID NULL | |
| metadata | JSONB NULL | nunca contiene secretos |
| ip_address | VARCHAR(45) NULL | |
| user_agent | VARCHAR(255) NULL | |
| created_at | TIMESTAMPTZ NOT NULL | |

Acciones auditadas en esta entrega: `LOGIN_SUCCESS`, `LOGIN_FAILURE`, `LOGOUT`, `CLIENT_CREATE`, `CLIENT_UPDATE`, `CLIENT_STATUS_CHANGE`, `CLIENT_OBLIGATION_ASSIGN`, `CLIENT_OBLIGATION_UPDATE`, `CLIENT_OBLIGATION_REMOVE`, `RUC_LOOKUP_QUERY`.

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
| is_good_taxpayer | BOOLEAN NOT NULL DEFAULT false | calidad de buen contribuyente |
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

## Diagrama de relaciones (esta entrega)

```
tenants 1──n users 1──n sessions
tenants 1──n clients 1──n client_obligations n──1 obligation_catalog
tenants 1──n audit_logs (n──1 users, opcional)
```

## Entidades planificadas para fases futuras (no implementadas aún)

`client_contacts`, `client_tax_profiles`, `client_credentials` (bóveda real), `client_settings`, `tax_schedule_sources`, `tax_schedule_versions`, `tax_schedule_entries`, `tax_schedule_exceptions`, `tax_periods`, `declarations`, `declaration_status_history`, `tax_assessments`, `tax_payments`, `document_requirements`, `document_requests`, `documents`, `professional_fee_agreements`, `professional_fee_charges`, `professional_fee_payments`, `receipts`, `communications`, `communication_templates`, `notifications`, `notification_deadlines`, `cases`, `tasks`, `calendar_events`, `drive_folders`, `email_messages`, `integration_accounts`, `integration_sync_runs`, `automation_jobs`, `backup_runs`, `security_events`, `ai_prompt_templates`, `ai_analysis_results`, `system_settings`, `roles`, `user_roles`.

Estas tablas se diseñarán en detalle y se migrarán en los bloques de Fase 1 posteriores y en las Fases 2–6, siguiendo las mismas convenciones (`tenant_id`, sin `float` para montos, timestamps con zona horaria, historial de estados donde aplique).
