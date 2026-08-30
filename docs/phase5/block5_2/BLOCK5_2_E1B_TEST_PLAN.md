# Block 5.2 — Domain E1-B: Plan de pruebas (diseñado antes de implementar)

**Este documento es un plan.** Ninguna de estas pruebas existe todavía
— Domain E1-A es arquitectura/docs únicamente. Complementa
`BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md` (contrato/orden/freeze) y
sigue el mismo patrón de rigor de pruebas ya productivo en
`backend/tests/integration/broker/` (Domain D/D.1): PostgreSQL real,
mTLS real con certificados efímeros, `threading.Barrier`/eventos reales
para carreras — nunca solo un lock de Python como única autoridad,
nunca solo aserciones sobre configuración.

Convención de nombres propuesta:
`backend/tests/integration/broker/test_broker_secret_delivery_*.py` +
`backend/tests/unit/test_secret_delivery_grant_registry.py`
(mismo patrón de separación unit/integration ya usado en Domain D).

## Matriz de pruebas (A–W, sección 56 del encargo)

| # | Escenario | Resultado exigido | Naturaleza |
|---|---|---|---|
| A | Vault bloqueada (sin `VaultUnlockRegistry` vigente para el `user_id`/`app_session_id` del grant) | Sin decrypt, sin consumo de gate, rechazo genérico | Integración, PostgreSQL real |
| B | Grant ausente (nunca autorizado) | Sin decrypt, sin gate | Integración, mTLS real |
| C | Grant vencido por TTL | Sin decrypt, sin gate | Integración — reloj monotónico de proceso, no `clock.set_clock` |
| D | Agente equivocado (mTLS válido pero job asignado a otro `agent_id`) | Sin decrypt, sin gate; rechazo genérico (nunca revela "existe pero no es tuyo") | Integración, dos identidades mTLS reales |
| E | Conexión obsoleta (`connection_id` reemplazado por un `HELLO` posterior del mismo certificado, ver `test_broker_logical_connection_identity.py::test_C` como precedente directo) | Sin decrypt, sin gate | Integración — dos instancias de agente de prueba compartiendo certificado |
| F | Agente revocado (`ACTIVE` al iniciar, `REVOKED` antes de la respuesta) | Sin decrypt/envío tras conocerse la revocación | Integración — revocar entre dos llamadas reales |
| G | Sesión `CANCELLED` (carrera real con `cancel_session`, dos conexiones PostgreSQL) | Sin decrypt, sin gate si la cancelación gana primero | Integración, dos conexiones reales (patrón `test_sol_agent_access_service_concurrency.py`) |
| H | Sesión `EXPIRED` | Igual que G, vía `expire_session`/`clock.db_now` | Integración |
| I | Job no `CLAIMED` (`PENDING`/`COMPLETED`/`FAILED`/`CANCELLED`/`EXPIRED`) | Sin decrypt, sin gate, para cada estado no-`CLAIMED` | Integración, parametrizada por estado |
| J | Camino feliz | Decrypt exactamente una vez, gate exactamente una vez, respuesta exactamente una vez, campos = `TEST_SOL_USER`/`TEST_SOL_PASSWORD_DO_NOT_USE` | Integración, PostgreSQL + mTLS reales |
| K | Dos solicitudes de entrega concurrentes, mismo agente/job | Exactamente una respuesta con texto plano; la otra, resultado genérico de ya-consumido | Integración, **dos conexiones PostgreSQL reales** + `threading.Barrier`, nunca solo lock de Python (sección 34/56 del encargo) |
| L | Fallo de BD después de decrypt, antes del commit del gate (mismo patrón de espía que `test_broker_certificate_issuance_order.py::test_G`) | Cero respuestas con texto plano; gate no consumido | Integración, `session.commit` reemplazado por un stub que falla una vez |
| M | Fallo de escritura de la respuesta después del commit del gate | Gate consumido; sin reintento/redelivery | Integración — simular corte de socket tras el commit, antes de `_send_json` |
| N | Carrera de reemplazo de conexión durante la ventana crítica (después del pin, antes de la escritura) | La conexión obsoleta no recibe el secreto | Integración — requiere el mecanismo de lease/pin de la sección 8 del documento de arquitectura; **prueba de diseño, no implementable literalmente hasta que el pin exista** |
| O | Carrera de revocación (revocar exactamente durante la ventana crítica) | La conexión revocada no recibe el secreto tras ganar la revocación | Integración, mismo patrón de sincronización que N |
| P | Carrera cancelación-vs-entrega | Ver matriz de la sección 16.3 del documento de arquitectura — el primero en comprometerse (transición terminal vs. commit del gate) determina el resultado, nunca ambos | Integración, dos conexiones reales |
| Q | Reinicio antes del gate | Requiere nueva autorización humana (grant no sobrevive) según lo congelado | Integración — dos instancias de `BrokerServer`, mismo patrón que `test_broker_restart_recovery.py` |
| R | Reinicio después del gate | Sin redelivery — nueva sesión SOL requerida | Integración, mismo patrón que Q |
| S | Ausencia del secreto en logs/auditoría/excepciones | Barrido de `caplog` (mismo patrón que `test_broker_log_redaction.py`) confirma que `sol_username`/`sol_password`/valores de prueba nunca aparecen en ninguna línea capturada | Integración |
| T | Ausencia del secreto en BD fuera de la credencial ya cifrada existente | Consulta de auditoría/`sol_agent_jobs`/`sol_access_sessions`/tablas de grant (si alguna llega a persistirse, lo cual está prohibido) confirma cero texto plano | Integración, PostgreSQL real |
| U | El registro de grant no contiene secreto/clave | Inspección directa de la estructura en memoria del `SecretDeliveryGrantRegistry` tras crear un grant real | Unitaria |
| V | Requisitos reales de `connection_id` + mTLS (sin atajos) | Repetir la matriz TLS A–G de Domain D (`test_broker_mtls_matrix.py`) contra el nuevo endpoint específicamente, más el chequeo de `connection_id` (`test_broker_logical_connection_identity.py`) | Integración, reutilizando los helpers ya existentes de `tests/integration/broker/` |
| W | Sin confusión entre token de job y "grant"/secreto | Prueba explícita de que el token de job (Domain C) y el grant de entrega (Domain E1) son conceptos y almacenes completamente separados — un token de job válido nunca autoriza por sí solo una entrega de secreto, y viceversa | Integración/unitaria |

## Escenarios adicionales (X–AD, Domain E1-A.1)

Añadidos tras el freeze de la máquina de estados del grant (`AUTHORIZED
→ IN_FLIGHT → CONSUMED/INVALIDATED`, sección 5.4 del documento de
arquitectura) y la confirmación de la fuente del RUC (sección 9).

| # | Escenario | Resultado exigido | Naturaleza |
|---|---|---|---|
| X | Dos solicitudes simultáneas, mismo `(tenant_id, job_id)` | Exactamente una adquiere `IN_FLIGHT`; la otra se rechaza **antes** de intentar descifrar | Integración, **dos conexiones reales** + `threading.Barrier` sobre el registro de grants (nunca solo secuencial) |
| Y | Mismo escenario que X | La función de descifrado (`decrypt_credential_field`, espiada con `unittest.mock.patch`, mismo patrón que `_perform_ca_signature` en Domain D.1) se invoca **exactamente una vez** entre las dos solicitudes | Integración |
| Z | Solicitud con agente/tenant/job equivocado, mientras un grant legítimo de OTRO agente está `AUTHORIZED` | El grant legítimo permanece intacto (`AUTHORIZED`, no tocado); la solicitud equivocada se rechaza sin mutar el registro en absoluto | Integración/unitaria — inspeccionar el registro antes y después |
| AA | Simular fallo del registro durable de auditoría (`CREDENTIAL_DELIVERY_AUTHORIZED`) durante la creación del grant | Ningún grant queda disponible tras el fallo — `AUDIT_FAILURE_CAN_LEAVE_ACTIVE_GRANT=NO` | Integración, mismo patrón de espía de commit que `test_broker_certificate_issuance_order.py::test_G` |
| AB | Intentar resolver `VaultUnlockRegistry` con `operation=VERIFY_ACCESS` en el flujo de entrega | Rechazo — solo `operation=CREDENTIAL_DELIVERY` autoriza la entrega; `VERIFY_ACCESS` nunca es suficiente | Integración/unitaria |
| AC | RUC en la respuesta de entrega | Coincide exactamente con `Client.ruc` de la cadena `job→session.client_id→Client`; ningún campo de la petición del agente puede sustituirlo (probar explícitamente que un valor de RUC "inyectado" por el agente en cualquier campo del protocolo es ignorado/rechazado, nunca reflejado) | Integración |
| AD | Ausencia de RUC/username/password en logs/auditoría/excepciones | Extiende la prueba S — el barrido de `caplog` cubre también el RUC de prueba (RUC sintético vía `fake_ruc()`, nunca uno real) | Integración |

## Notas de diseño de pruebas

- **K (concurrencia real)** es la prueba más crítica de todo Domain
  E1-B: debe replicar exactamente el rigor ya usado en
  `test_sol_agent_access_service_concurrency.py` (dos `sessionmaker`
  independientes, `threading.Barrier`, aserción de exactamente un
  ganador) — **nunca** un test que solo llama la función dos veces
  secuencialmente y confía en que el índice único de BD "probablemente"
  la protegería.
- **N/O** dependen de que el mecanismo de lease/pin (sección 8 del
  documento de arquitectura) exista — son pruebas de **diseño**
  incluidas aquí para que la implementación de E1-B las tenga como
  criterio de aceptación desde el principio, no como una idea tardía.
- Ninguna prueba de este plan debe usar una credencial SOL real ni un
  RUC real — únicamente `TEST_SOL_USER`/`TEST_SOL_PASSWORD_DO_NOT_USE`
  y RUCs sintéticos ya generados por `compute_check_digit`
  (`tests/integration/_sol_agent_access_helpers.py::fake_ruc`, patrón
  ya productivo).
- El arnés de agente de prueba (`AgentTestClient`, ya productivo en
  Domain D) se extiende con un método `request_credential(job_id)` —
  arnés de prueba únicamente, nunca Agente Windows productivo, mismo
  principio ya congelado en Domain D.
- **X/Y/Z** son tan críticas como K: prueban la reserva atómica
  `IN_FLIGHT` en sí (X/Y) y la disciplina de "nunca mutar el registro
  antes de localizar el grant correcto" (Z, sección 5.4 del documento
  de arquitectura) — sin Z, un atacante podría usar identidades
  incorrectas como primitivo de denegación de servicio contra el grant
  de otro agente.
