# Block 5.2 — Arquitectura de producto: Supervised SOL Access

**Domain A — Architecture & Contract Freeze**, extendido en **Domain
A.1 — Persistence & Concurrency Contract Freeze** y en **Domain A.2 —
Final DDL Security & Compatibility Gate** (ver
`BLOCK5_2_DATA_MODEL_DRAFT.md` para el DDL exacto — Domain A.2
confirmó/corrigió DDL y mecanismos de seguridad puntuales, sin tocar
nada de lo congelado en esta página: alcance, frontera del Buzón,
arquitectura agente/broker y política de portal quedan sin cambios).
Documento de diseño, sin código ni
migración. Congela el alcance, las fronteras duras y la arquitectura
de conexión antes de escribir migración `0018` o implementación
significativa.

Ubicación deliberada: `docs/phase5/block5_2/` es una carpeta **nueva**
en este repositorio — Bloque 5.1 no usó una carpeta por bloque (vivió
como ADR-0068..0071 + una entrada de `CHANGELOG.md` + un párrafo de
`ROADMAP.md`). Se introduce aquí porque el freeze de Bloque 5.2 abarca
8 documentos sustanciales de diseño (arquitectura, máquina de estados,
modelo de seguridad, contratos de API, borrador de datos, matriz RBAC,
registro de evidencia, plan de implementación) — más contenido
coherente del que un único ADR debería cargar. Los ADR números
reservados (ver `BLOCK5_2_IMPLEMENTATION_PLAN.md`) seguirán viviendo en
`docs/DECISIONS/` cuando se escriban, como siempre.

## 1. Estado canónico de partida

```
master (productivo): 7dd422c54a8a932e8a9efa76675a0b3bc6122958
  = Phase 5 / Block 5.1 "Secure SOL Credential Vault" v0.9.0-alpha.1

rama de este freeze: feat/phase5-block2-supervised-sol-access
  (creada desde master, sin divergencia todavía)

Spike A: spike/phase5-sol-portal-recon
  final: b274435c2b3326f4f98268e9b8330534124606fa
  tag:   spike-phase5-sol-portal-recon-v1
  status: CLOSED/PASS_WITH_DEFERRED_UNKNOWNS

Spike B: spike/phase5-sol-agent-ipc
  commit: 542b167049bd46f9d002bcfcd96aed16d77c77e1
  tag:    spike-phase5-sol-agent-ipc-v1
  status: CLOSED/PASS
```

**Spike A y Spike B son evidencia, no código a fusionar.** Ningún
archivo de ninguno de los dos spikes se copia, importa, ni fusiona a
esta rama — la implementación productiva se escribe desde cero sobre
`master`, usando los spikes exclusivamente como especificación
validada empíricamente. Ver `BLOCK5_2_EVIDENCE_REGISTER.md` para la
trazabilidad exacta decisión → fuente.

## 2. Alcance productivo inicial

```
SOL_ACCESS_SCOPE = AUTHENTICATE_TO_LANDING_ONLY
```

La primera versión productiva de Block 5.2 puede:

1. Crear una sesión SOL supervisada.
2. Esperar un agente Windows disponible.
3. Asignar el job al agente.
4. Entregar credenciales puntualmente (nunca persistentes en el agente).
5. Abrir Chromium **visible** (nunca headless).
6. Validar el host de autenticación (exact-match, ver sección 6).
7. Seleccionar modo de login RUC.
8. Introducir credenciales mediante el agente, dentro del navegador.
9. Detenerse ante challenge humano (`WAITING_FOR_USER`).
10. Permitir resolución humana del challenge.
11. Continuar la autenticación tras la resolución.
12. Reconciliar Pages (identidad Page/Frame, nunca asumida — Spike A).
13. Llegar a `e-menu.sunat.gob.pe`.
14. Requerir confirmación humana explícita del contribuyente correcto.
15. Terminar la sesión mediante logout.
16. Cerrar el `BrowserContext`.
17. Limpiar residuos (procesos, perfiles temporales).
18. Registrar auditoría sanitizada.

**No puede** (sin excepción, ver `BLOCK5_2_SECURITY_MODEL.md` T14):

```
OPEN_BUZON
LIST_BUZON_ITEMS
OPEN_MESSAGE
OPEN_DOCUMENT
DOWNLOAD_DOCUMENT
MARK_READ
MARK_UNREAD
ACKNOWLEDGE
RESPOND
FILE_DECLARATION
RECTIFY
PAY
SUBMIT
REGISTER
MODIFY_REMOTE_DATA
```

## 3. Frontera dura de producto

El menú Buzón **puede ser visible** (Spike A A1 lo demostró:
`BUZON_MENU_VISIBLE=VALIDATED`, real, sin necesidad de clic). Eso
**no autoriza**: clic, hover con side effect, llamada a endpoint,
conteo, lectura de asunto, listado, apertura de mensaje, ni descarga.

Cualquier navegación funcional fuera del landing autorizado:

```
OUT_OF_SCOPE_ACTION → fail closed
```

No existe un modo "best effort" para esta frontera — es binaria: o la
Page/acción está dentro de `AUTHENTICATE_TO_LANDING_ONLY`, o el flujo
aborta.

## 4. Arquitectura agente/broker

Hereda la arquitectura ya validada por Spike B (checkpoint
`spike-phase5-sol-agent-ipc-v1`) — **no se rediseña aquí**:

```
Windows Agent (nativo, sesión de escritorio visible)
    → INICIA la conexión
    → Broker (Docker/Linux, publicado únicamente en 127.0.0.1)
```

- **Nunca** al revés: el backend no abre una conexión arbitraria hacia
  el agente.
- Transporte: **mTLS** obligatorio. Nunca entrega de secretos por HTTP
  en texto plano.
- El broker es un componente de infraestructura nuevo para este repo
  — hoy no existe código de mTLS/pairing/broker en `backend/app`
  (confirmado por barrido — ver `BLOCK5_2_EVIDENCE_REGISTER.md`). Se
  construye desde cero en Domain B/C, siguiendo el contrato aquí
  congelado.

## 5. Pairing (diseño, basado en Spike B)

```
agent genera key/CSR
  ↓
pairing request
  ↓
código humano de 6 dígitos
  ↓
TTL corto
  ↓
certificado de CA local
  ↓
identidad del agente establecida
```

Parámetros congelados (revisables en el futuro, **nunca relajados
automáticamente**):

```
PAIRING_CODE_TTL_SECONDS = 120
PAIRING_MAX_FAILED_ATTEMPTS = 5
```

Al superar el máximo de fallos: **lockout permanente de ese intento de
pairing** (no del agente ni del tenant en general — un nuevo intento
de pairing requiere iniciar el flujo de nuevo desde cero, con un nuevo
código). Ver `BLOCK5_2_DATA_MODEL_DRAFT.md` sección "pairing" para la
discusión de si esto necesita tabla propia.

## 6. Política de portal (heredada de Spike A, sin relajar)

| Rol | Host | Coincidencia |
|---|---|---|
| `PUBLIC` | `sunat.gob.pe`, `www.sunat.gob.pe` | exact-match |
| `AUTH` | `api-seguridad.sunat.gob.pe` | exact-match |
| `LANDING` | `e-menu.sunat.gob.pe` | exact-match |

**Nunca wildcard, nunca `*.sunat.gob.pe`.** Identidad Page/Frame
(`context.pages` → `page.main_frame`) es la única fuente de verdad
para "esto es una Page top-level autorizada" — la observación de red
(`resource_type`, método HTTP) es **telemetría, nunca autorización**
(regla `DOCUMENT != TOP_LEVEL_PAGE`, congelada en Spike A / A2-TD).

## 7. `PORTAL_CHANGED`

Dispara ante cualquiera de:

- Host inesperado.
- Ambigüedad de superficie (más de una Page candidata).
- Page de auth no identificable.
- Page post-auth no identificable.
- Un iframe sustituye a la Page esperada.
- El flujo estructural no coincide con lo esperado.

Resultado siempre: **`PORTAL_CHANGED` → fail closed.** Nunca
"best effort", nunca heurística de "última pestaña", nunca por título,
nunca por foco, nunca wildcard de host.

## 8. Ciclo de vida del navegador

- `BrowserContext` **efímero** — nunca perfil persistente.
- **Nunca** `storageState`, cookies guardadas, sesión guardada, ni
  directorio `user_data_dir` reutilizable.
- Al terminar (éxito, fallo, o cancelación): `context.close()` →
  `browser.close()` → verificación de cleanup (procesos huérfanos,
  residuos temporales — mismo patrón que `cleanup-check` de Spike A).

## 9. Human wait (interfaz abstracta para producto)

Spike A validó empíricamente el patrón: un hilo lector (stdin/UI)
señaliza vía evento; el hilo **dueño** de Playwright sigue bombeando
llamadas reales (`page.wait_for_timeout(...)`) mientras espera —
nunca bloquea Playwright con una espera puramente Python
(`input()`/`time.sleep()` sin más).

Para producto, la espera humana **no necesariamente es stdin** — el
frontend/API resuelve la espera. Se diseña una interfaz abstracta:

```
HumanInteractionGateway
    .wait_for(prompt, timeout, is_cancelled) -> resultado
```

cuya implementación productiva puede ser "esperar un POST del
frontend a un endpoint de resolución de sesión" en vez de "leer
stdin" — pero el **bucle propietario de Playwright debe seguir vivo**
mientras se espera esa señal, exactamente como en Spike A. No se
implementa esta interfaz en Domain A — solo se congela el contrato.

## 10. Descargas, popups y frames

- **Descargas deshabilitadas** en el alcance inicial: `page.on("download")`
  → cancelar inmediatamente → `fail closed` → la sesión termina en
  `FAILED` o `PARTIAL_FAILURE` según el estado en que ocurrió. **Nunca
  se crea un archivo en disco.**
- **Page top-level**: clasificación estricta (identidad Page/Frame,
  Spike A) — cualquier Page nueva no esperada → abort.
- **Child frame**: solo se observa metadata (scheme/hostname/rol) —
  nunca se interactúa automáticamente con su contenido.
- **Frame inesperado que afecta auth/landing** (ej. un iframe
  sustituye la Page de login o de landing esperada) → abort.

## 11. Exclusión explícita del Buzón del diseño de producto

El diseño de Block 5.2 debe impedir, **incluso accidentalmente**, que
el alcance inicial contenga:

- Parser de endpoints del Buzón.
- Modelo de mensaje.
- Tabla de ítems de bandeja de entrada.
- Conteo de mensajes.
- Endpoint de descarga.
- Localizadores DOM de mensajes.

`SolInboxItem` **no se modela en producto todavía** — sigue siendo un
borrador provisional documentado únicamente en
`spikes/sol_portal_recon/a2_doc/A2_TECHNICAL_UNKNOWN_MATRIX.md` (Spike
A, A2-DOC), no en `backend/app/models/`.
