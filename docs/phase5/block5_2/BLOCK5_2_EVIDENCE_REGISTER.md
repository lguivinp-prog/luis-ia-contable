# Block 5.2 — Registro de evidencia

**Domain A — Architecture & Contract Freeze**, ampliado en **Domain
A.1 — Persistence & Concurrency Contract Freeze**, en **Domain A.2 —
Final DDL Security & Compatibility Gate** (4 filas nuevas al final de
la tabla, todas evidencia BLOCK 5.1 directa contra código productivo
real), y corregido en **Domain A.3 — Pairing Verifier Cryptographic
Freeze**: la fila de Domain A.2 sobre el mecanismo del verificador de
pairing se marca `SUPERSEDED` (afirmaba que Argon2id era un *keyed
verifier* — falso, `PasswordHasher()` no toma pepper/secreto) y se
reemplaza por 2 filas nuevas con la evidencia real que sustenta el
HMAC-SHA-256 keyed final. Traza cada decisión
de diseño de los otros 7 documentos a su fuente exacta. Distingue
explícitamente **SPIKE A** / **SPIKE B** / **BLOCK 5.1** (código
productivo real, ya en `master`) / **INFERENCE** (deducción razonada
de este documento, sin validación empírica directa) — **ninguna
decisión `INFERENCE` se presenta como empíricamente validada**.

| Decisión | Fuente | Evidencia concreta | Consecuencia de producto |
|---|---|---|---|
| Agente Windows nativo, visible (nunca headless) | SPIKE A | A1 smoke real: `pw.chromium.launch(headless=False)`, ejecutado por el humano | `BLOCK5_2_ARCHITECTURE.md` sección 2, ítem 5 |
| `BrowserContext` efímero, sin `storage_state` | SPIKE A | A1 código + smoke real: `BROWSER_PROCESSES_LEFT=0` tras cerrar, dos veces | `BLOCK5_2_ARCHITECTURE.md` sección 8 |
| Human challenge `HUMAN_ONLY`, sin OCR/solver | SPIKE A | `observer/manual_auth.py`, smoke real `AUTH_COMPLETED_BY_HUMAN=YES` | `BLOCK5_2_STATE_MACHINE.md` sección 4 |
| Cancelación/timeout de esperas humanas | SPIKE A | `observer/human_wait.py`, validado con Playwright real (`test_human_wait_real_playwright.py`) | `BLOCK5_2_ARCHITECTURE.md` sección 9 |
| Fail-closed ante cierre inesperado del navegador | SPIKE A | `_wait_for_human_with_pump`, `browser.is_connected()` | `BLOCK5_2_STATE_MACHINE.md` sección 6 |
| Host de auth exact-match, sin wildcard | SPIKE A | `observer/host_allowlist.py`, smoke real `AUTH_HOST_VALIDATED=YES` | `BLOCK5_2_ARCHITECTURE.md` sección 6 |
| Login por RUC | SPIKE A | Smoke real: `LOGIN_MODE=RUC` | `BLOCK5_2_ARCHITECTURE.md` sección 2, ítem 7 |
| Reconciliación de Page/Frame, nunca asumida | SPIKE A | `observer/post_auth_surface.py`, corrección `DOCUMENT != TOP_LEVEL_PAGE` (A2-TD) | `BLOCK5_2_ARCHITECTURE.md` secciones 6-7 |
| Landing real en `e-menu.sunat.gob.pe` | SPIKE A | Smoke real: `POST_AUTH_HOST=e-menu.sunat.gob.pe`, `POST_AUTH_HOST_CONFIRMED=YES` | `BLOCK5_2_ARCHITECTURE.md` sección 6 |
| Confirmación humana de contribuyente, sin pedir RUC/nombre | SPIKE A | `observer/wrong_taxpayer_guard.py`, smoke real `TAXPAYER_CONTEXT_CONFIRMED=YES`; `RISK_REGISTER.md` T9 `evidence_status=VALIDATED`, **residual MEDIA sin reducir** | `BLOCK5_2_STATE_MACHINE.md` sección 5 — el residual T9 se hereda tal cual, ver T8 en `BLOCK5_2_SECURITY_MODEL.md` threat model |
| Logout observado | SPIKE A | Smoke real: `LOGOUT_OBSERVED=YES` | `BLOCK5_2_ARCHITECTURE.md` sección 2, ítem 15 |
| Cleanup en cero | SPIKE A | Smoke real, dos veces: `BROWSER_PROCESSES_LEFT=0`, `TEMP_PROFILE_RESIDUES=0`, `STORAGE_STATE_FILES=0` | `BLOCK5_2_ARCHITECTURE.md` sección 8 |
| `PORTAL_CHANGED` fail-closed | SPIKE A | `observer/portal_changed.py`; reforzado por `A2_TD_THREAT_MODEL.md` (hard stops) | `BLOCK5_2_ARCHITECTURE.md` sección 7 |
| `GET != READ_ONLY GUARANTEE` | SPIKE A | A2-TD, escenario B del fake portal: mutación server-side invisible al cliente, empíricamente demostrada | `BLOCK5_2_API_CONTRACTS.md` sección 7 (aplicado en sentido inverso: el propio backend debe garantizar lo que SUNAT no garantiza) |
| `NO_OBSERVABLE_MUTATION != NO_SERVER_SIDE_MUTATION` | SPIKE A | A2-TD, `INVISIBLE_SERVER_SIDE_MUTATION_DETECTION_LIMIT=VALIDATED` | `BLOCK5_2_SECURITY_MODEL.md` T17 — motivo explícito de por qué el Buzón permanece fuera de alcance |
| Frontera dura del Buzón (visible ≠ autorizado) | SPIKE A | A1 smoke real: `BUZON_MENU_VISIBLE=VALIDATED`, `BUZON_OPENED=NO`; A2-DOC `A2_DECISION_GATE.md` `A2_1_RECOMMENDED=NO` | `BLOCK5_2_ARCHITECTURE.md` secciones 3 y 11 |
| Tensión "efectuada vs. surte efectos" sin resolver | SPIKE A | A2-DOC `A2_LEGAL_ANALYSIS.md` — investigación jurídica, sin opinión legal experta adicional | `PRODUCT_HANDOFF_PHASE5_BLOCK2.md` (spike) sección `DEFERRED`; no bloquea `AUTHENTICATE_TO_LANDING_ONLY` porque ese scope no depende de la fecha de efectos de una notificación |
| Conexión iniciada por el agente hacia el broker | SPIKE B | Checkpoint `spike-phase5-sol-agent-ipc-v1` | `BLOCK5_2_ARCHITECTURE.md` sección 4 |
| Broker solo `127.0.0.1` | SPIKE B | Checkpoint `spike-phase5-sol-agent-ipc-v1` | `BLOCK5_2_ARCHITECTURE.md` sección 4 |
| mTLS obligatorio | SPIKE B | Checkpoint `spike-phase5-sol-agent-ipc-v1` | `BLOCK5_2_ARCHITECTURE.md` sección 4; `BLOCK5_2_SECURITY_MODEL.md` sección 1 |
| Pairing: código de 6 dígitos, TTL 120s, máx. 5 fallos | SPIKE B | Checkpoint `spike-phase5-sol-agent-ipc-v1` — **parámetros citados tal cual del checkpoint de esa rama, no re-verificados en esta sesión** (rama no re-inspeccionada) | `BLOCK5_2_ARCHITECTURE.md` sección 5 |
| Alternative A — entrega puntual de credenciales | SPIKE B | Checkpoint `spike-phase5-sol-agent-ipc-v1` — ídem, citado sin re-verificar | `BLOCK5_2_SECURITY_MODEL.md` sección 1 |
| Agente nunca recibe `vault_root_key`/`credential_dek` | SPIKE B | Checkpoint `spike-phase5-sol-agent-ipc-v1` | `BLOCK5_2_SECURITY_MODEL.md` sección 1 |
| Vault de credenciales SOL, cifrado por slot | BLOCK 5.1 | Código productivo real en `master`: `backend/app/core/sol_vault_crypto.py`, `backend/app/services/sol_vault_service.py` — verificado en esta sesión | `BLOCK5_2_SECURITY_MODEL.md` sección 2 |
| `SolCredentialStatus` ya reserva valores para 5.2 | BLOCK 5.1 | `backend/app/models/client_sol_credential.py` — docstring explícito de que 5.2 activará valores no alcanzables por código de 5.1 | `BLOCK5_2_DATA_MODEL_DRAFT.md` — el estado de la credencial (no de la sesión) se hereda, no se rediseña |
| Patrón de máquina de estados (enum `str` + `CheckConstraint` + tabla de transiciones + `row_version`) | BLOCK 5.1 (y anteriores) | `backend/app/models/declaration.py` + `backend/app/services/declaration_service.py`, ADR-0018/ADR-0022 — código productivo real | `BLOCK5_2_STATE_MACHINE.md` completo |
| Patrón de aislamiento de tenant (FK compuesta) | BLOCK 5.1 (y anteriores) | `backend/app/models/client_sol_credential.py`, ADR-0003/ADR-0024 — código productivo real | `BLOCK5_2_SECURITY_MODEL.md` sección 6, `BLOCK5_2_DATA_MODEL_DRAFT.md` completo |
| Patrón de `Permission`/RBAC (enum cerrado + frozensets por rol) | BLOCK 5.1 (y anteriores) | `backend/app/core/permissions.py`, ADR-0050/ADR-0052 — verificado directamente: admin=51/contador=44/asistente=22 | `BLOCK5_2_RBAC_MATRIX.md` completo |
| Patrón de `AuditAction`/`audit_logs` | BLOCK 5.1 (y anteriores) | `backend/app/models/audit_log.py` — 16 acciones SOL-vault ya agregadas en 5.1 con el mismo patrón de banner | `BLOCK5_2_SECURITY_MODEL.md` sección 7 |
| `sol_agent_pairings` como tabla independiente (no fusionada con `sol_agents`) | **INFERENCE, congelada en Domain A.1** | Razonamiento de este documento (`BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4) — **sin precedente directo en el repo ni en ningún spike** | `PAIRING_TABLE_DECISION=YES`, definitivo — ya no es recomendación pendiente, forma parte de las 4 tablas de la migración `0018` |
| `idempotency_key: uuid.UUID` como columna en la propia entidad, no header HTTP | BLOCK 5.1 (y anteriores) | `backend/app/services/tax_payment_service.py` — `IdempotencyKeyConflictError`, `get_by_idempotency_key(db, tenant_id, idempotency_key)`, patrón verificado en código productivo real esta ronda (Domain A.1) | `BLOCK5_2_API_CONTRACTS.md` sección 7; `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 1 (`sol_access_sessions.idempotency_key`) — corrige la suposición inicial de Domain A de un header `Idempotency-Key` |
| Rate-limit de creación de pairing por conteo de filas + backoff exponencial (sin Redis) | BLOCK 5.1 (y anteriores) | `backend/app/services/login_attempt_service.py` (ADR-0009) — `get_lockout_status()`, verificado en código productivo real esta ronda (Domain A.1) | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4 ("Fuerza bruta más allá del límite por registro") — capa secundaria sobre `sol_agent_pairings`, sin tabla nueva |
| `OFFLINE` derivado de `last_seen_at`, no persistido | **INFERENCE** | Razonamiento de este documento (`BLOCK5_2_DATA_MODEL_DRAFT.md` sección 2) — sin precedente directo | Ídem — pendiente de confirmación |
| Elección de transporte agente↔broker (WebSocket/long-poll/stream) | **INFERENCE — deliberadamente no resuelta** | Ninguna fuente decide esto — Spike B no llegó a esa capa de detalle según lo citado | Explícitamente diferida a Domain B en `BLOCK5_2_API_CONTRACTS.md` sección 1 y `BLOCK5_2_IMPLEMENTATION_PLAN.md` |
| `sol_access_audit_events` como tabla separada | **Descartado por INFERENCE, confirmado en Domain A.1** | El encargo original lo proponía como candidato; este documento concluye que duplicaría `audit_logs` sin necesidad | `AUDIT_STORAGE_MODEL=EXISTING_AUDIT_INFRASTRUCTURE` — `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 8, `BLOCK5_2_SECURITY_MODEL.md` sección 7 |
| `client_sol_credentials` ya soporta el FK compuesto `(id, tenant_id, client_id)` que necesita `sol_access_sessions` — sin cambio a esa tabla | BLOCK 5.1 | `backend/app/models/client_sol_credential.py` líneas 90-92: `UniqueConstraint("id", "tenant_id", "client_id", name="uq_client_sol_credentials_id_tenant_client")`, ya creado en `backend/migrations/versions/0017_sol_vault.py` líneas 299-300 (migración ya aplicada, Alembic head=0017) — verificado directamente contra el modelo y la migración real esta ronda (Domain A.2, Gate 1) | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 6 — `CREDENTIAL_COMPOSITE_FK_SUPPORTED=YES`, `CREDENTIAL_SUPPORTING_UNIQUE_REQUIRED=NO` |
| ~~`pairing_code_verifier` usa Argon2id vía la utilidad ya productiva de contraseñas~~ — **`SUPERSEDED` en Domain A.3** | BLOCK 5.1 (evidencia real, **conclusión incorrecta**) | `backend/app/core/security.py` fue leído en su totalidad en Domain A.2, pero la lectura no verificó si `PasswordHasher()` toma un parámetro de secreto/pepper — la conclusión `PAIRING_CODE_KEYED_VERIFIER=YES` no se sostenía con la evidencia citada. Corregido en Domain A.3 (ver las 2 filas siguientes) | Reemplazada — ver `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4 |
| `_password_hasher = PasswordHasher()` se instancia **sin** parámetro de secreto/pepper — Argon2id *salted*, no *keyed*; ningún pepper/secret-key existe en ningún punto de `backend/` | BLOCK 5.1 | `backend/app/core/security.py` línea 8; barrido `pepper\|secret_key\|PasswordHasher\(` sobre todo `backend/app/core/` sin resultados adicionales — verificado esta ronda (Domain A.3, Gate 2 corrección) | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4 — `ARGON2_PASSWORD_HASH_IS_KEYED=NO`; motivo del reemplazo por HMAC-SHA-256 keyed |
| Patrón de gestión de secretos servidor-side fuera de BD, con clave de propósito específico y precedente de fail-closed ante configuración faltante | BLOCK 5.1 (y anteriores) | `backend/app/core/config.py` — `Settings(BaseSettings)`, `env_file=".env"`; `session_secret: str` (obligatorio, sin default); `integration_token_encryption_key`/`integration_token_key_id` (ADR-0064) — vacío por defecto, falla cerrado (`INTEGRATION_MISCONFIGURED`) en `backend/app/core/integration_crypto.py`, nunca silencioso — leídos esta ronda (Domain A.3, Gate 6) | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4 — patrón reutilizado para `PAIRING_VERIFIER_KEY` (ajuste nuevo, propósito específico, no reutiliza literalmente `session_secret` ni `integration_token_encryption_key`); y para descartar `pairing_verifier_key_version` como columna (el precedente `integration_token_key_id` existe porque cifra secretos de vida larga, caso que no aplica a un verificador con TTL de 120s) |
| Patrón de idempotencia real: sin columna de fingerprint/hash de payload — la comparación de "mismo payload" se hace directamente contra las columnas de negocio de la entidad ya persistida | BLOCK 5.1 | `backend/app/services/tax_payment_service.py` — `_idempotency_payload_matches(payment, payload)` compara campos del payload contra columnas del `TaxPayment` ya guardado, sin ningún hash intermedio; `backend/app/models/tax_payment.py` línea 50 confirma el scope exacto `UniqueConstraint("tenant_id", "idempotency_key", name="uq_tax_payments_idempotency_key")` — releído esta ronda (Domain A.2, Gate 6/7) para confirmar (no solo citar de memoria de Domain A.1) | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 1 — `IDEMPOTENCY_PAYLOAD_FINGERPRINT_COLUMN_REQUIRED=NO`; `sol_access_sessions` no gana columna nueva por este motivo |
| Índice único parcial `UNIQUE(session_id) WHERE credential_delivered_at IS NOT NULL` en `sol_agent_jobs` cierra la carrera de re-entrega entre jobs distintos de la misma sesión, sin columna nueva | **INFERENCE, congelada en Domain A.2** | Razonamiento de este documento aplicando el mismo mecanismo de índice único parcial ya elegido en Domain A.1 para "máx. 1 job activo por sesión/agente" — sin precedente literal en el repo para este caso específico, pero mismo idioma PostgreSQL ya validado en esta misma migración | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 3 — `MAX_CREDENTIAL_DELIVERIES_PER_SESSION=1`, `CREDENTIAL_DELIVERY_CONCURRENCY_DB_SAFE=YES` |

## Nota sobre Spike B

**Spike B no fue re-verificado en esta sesión** — su rama
(`spike/phase5-sol-agent-ipc`) no fue fusionada, ni inspeccionada de
nuevo archivo por archivo; los puntos atribuidos a Spike B en esta
tabla se citan **tal como quedaron registrados en su propio checkpoint
(`spike-phase5-sol-agent-ipc-v1`)**, transmitidos como contexto de la
sesión. Si Domain B requiere confirmar el detalle exacto de esos
parámetros (ej. el mecanismo mTLS específico, el formato exacto del
CSR), debe re-consultarse esa rama directamente en lugar de asumir que
este registro los verificó de nuevo.
