# ADR-0067: Comunicaciones asistidas por WhatsApp — estados honestos, plantillas en código y enlace wa.me nunca persistido

## Estado
Aceptado

## Contexto
Fase 3, Bloque 3.2 introduce un mecanismo para redactar mensajes de WhatsApp dirigidos a un cliente, sin usar
WhatsApp Business API, WhatsApp Cloud API, ni ninguna forma de automatización o scraping de WhatsApp Web. El
envío real del mensaje siempre lo hace el usuario, a mano, desde su propia aplicación de WhatsApp. El sistema
estructuralmente **no puede** verificar si WhatsApp realmente abrió, si el mensaje se entregó, se recibió o se
leyó — cualquier estado o texto que implique esa confirmación sería una afirmación falsa.

A diferencia del Bloque 3.1 (Google OAuth), esta función no realiza ninguna llamada HTTP externa: construir un
enlace `https://wa.me/...` es una operación puramente local (concatenación + codificación de URL). Por lo
tanto **no aplica** la excepción de ADR-0065 (patrón preparar→llamar→persistir) — el servicio de este bloque
sigue ADR-0037 sin excepción: el router es dueño de toda la transacción de cada endpoint.

## Decisión

### Estados honestos, sin `OPENED_EXTERNALLY`
`communication_drafts.status` tiene 4 valores: `DRAFT`, `OPEN_REQUESTED`, `MARKED_SENT`, `DISCARDED`.
Deliberadamente **no existe** un estado `OPENED_EXTERNALLY` — el backend no tiene forma de saber si el
navegador realmente abrió WhatsApp tras `window.open()`, así que no se introduce un estado que sugiera esa
certeza. `OPEN_REQUESTED` significa exactamente "el usuario pulsó *Abrir en WhatsApp* y el backend construyó el
enlace" — nunca "WhatsApp se abrió". `MARKED_SENT` es una **autodeclaración humana**: el usuario confirma que
él mismo lo envió, el sistema no lo verifica. Terminología obligatoria en toda la UI: "Se solicitó abrir
WhatsApp", "Marcado como enviado por ti", "Pendiente de confirmación manual". Terminología prohibida en
cualquier parte de la UI: "WhatsApp abierto", "Mensaje enviado" (sin calificador humano), "Entregado",
"Recibido", "Leído", "Confirmado por WhatsApp" — verificado por una prueba automatizada
(`communicationTerminology.test.ts`) que escanea el código fuente real de las pantallas y falla si cualquiera
de esas frases aparece.

### Idempotencia real de `mark-open-requested`
Repetir la solicitud de apertura mientras el borrador ya está `OPEN_REQUESTED` es un no-op verdadero: no muta
ninguna columna, no incrementa `version`, no genera una segunda entrada de auditoría — solo recalcula y
devuelve la misma `wa_url` derivada de `body`/`recipient_phone_e164`, que nunca cambian tras la creación. Esto
permite que el usuario reintente sin miedo a "duplicar" el registro de la solicitud.

### FK tenant-safe hacia `declaration_document_files` vía UNIQUE auxiliar no destructiva
`declaration_document_files` ya tenía una UNIQUE de 6 columnas
(`uq_declaration_document_files_chain_scope`), demasiado ancha para servir de destino a una FK compuesta de 3
columnas `(id, tenant_id, client_id)`. La migración 0016 agrega `uq_declaration_document_files_id_tenant_client`
sobre esa misma tabla preexistente — no destructiva, porque `id` ya es PK (globalmente único), así que ninguna
fila existente puede violarla. Esta UNIQUE **debe reflejarse también en el modelo ORM**
(`DeclarationDocumentFile.__table_args__`), no solo en la migración: las pruebas construyen el esquema con
`Base.metadata.create_all()`, no con Alembic, y sin el `UniqueConstraint` en el modelo la FK de
`communication_drafts` fallaría con `InvalidForeignKey` bajo pruebas aunque la migración real fuera correcta
(mismo patrón ya documentado para el índice único parcial de esa tabla).

### Normalización conservadora de teléfono, con reglas de conveniencia solo para Perú
`normalize_whatsapp_number` nunca adivina un país fuera de Perú. Un número de 9 dígitos que empieza en "9" o de
11 dígitos con prefijo "51" se completa automáticamente a `+51...`; cualquier otro caso ambiguo se rechaza con
`INVALID_WHATSAPP_NUMBER`, exigiendo formato E.164 explícito (`+` seguido de 8 a 15 dígitos). Un número
ausente (ni en `clients.whatsapp_number` ni como override) es `WHATSAPP_NUMBER_REQUIRED`, distinto de un
número presente pero mal formado.

### Catálogo de plantillas versionado en código, no en una tabla genérica
Las 5 plantillas (`GENERAL_MESSAGE`, `OBLIGATION_DUE_REMINDER`, `DOCUMENT_REQUEST`,
`PAYMENT_PENDING_REMINDER`, `TASK_FOLLOW_UP`) son `CommunicationTemplate` congelados en
`app/domain/communication_templates.py`, cada uno con su propia allowlist de variables (subconjunto de
`ALLOWED_VARIABLES`, 7 variables seguras en total). Ninguna plantilla puede insertar automáticamente
contraseñas, credenciales SOL, tokens, datos bancarios ni el contenido íntegro de un documento — la allowlist
lo impide estructuralmente, no por convención. Una variable no declarada por la plantilla específica lanza
`UnknownTemplateVariableError`; una variable admitida pero no proporcionada se renderiza como cadena vacía
(nunca lanza), porque el texto queda de todas formas editable por el usuario antes de guardarse. Se decidió no
usar una tabla genérica editable en esta versión — mantiene el catálogo auditable en control de versiones y
evita el riesgo de que un usuario cree una plantilla con una variable insegura.

### El enlace `wa.me` nunca se persiste, solo se devuelve desde `mark-open-requested`
`_build_wa_url` construye `https://wa.me/<digitos-sin-mas>?text=<body-percent-encoded>` en el momento exacto
de `mark_open_requested`, a partir del `body` y `recipient_phone_e164` ya guardados en la fila — nunca se
guarda en ninguna columna de `communication_drafts` (embebería el texto completo del mensaje en una URL
persistida). Por la misma razón, `CommunicationDraftOut` (la forma expuesta por GET/list) omite
deliberadamente `recipient_phone_e164` — solo incluye `recipient_phone_display`, el número tal como el usuario
lo escribió, nunca el E.164 canónico que alimenta la URL. La codificación usa `urllib.parse.quote(body,
safe="")`, verificado explícitamente contra espacios, tildes, "ñ", saltos de línea, "?", "&" y otros
caracteres Unicode — ninguno debe aparecer literal en la query string.

### Frontend: `window.open` con fallback de ventana bloqueada, nunca `localStorage`/`sessionStorage`
El botón "Abrir en WhatsApp" llama primero a `mark-open-requested` (que ya transiciona el estado en el
backend, incluso si el navegador termina bloqueando la ventana emergente) y solo después intenta
`window.open(wa_url, "_blank", "noopener,noreferrer")`. Si el navegador bloquea la ventana (`window.open`
devuelve `null`), el estado ya quedó en `OPEN_REQUESTED` — no se revierte — y se ofrece un enlace `<a
href={wa_url}>` manual. `wa_url` vive únicamente en el estado de React de ese componente (memoria de la
pestaña); nunca se escribe en `localStorage` ni `sessionStorage`, ya que ambos sobreviven la sesión y
persistirían el texto completo del mensaje en el navegador del usuario.

## Alternativas consideradas
- **Un estado `SENT` sin calificar**: rechazado explícitamente — implicaría una confirmación de entrega que el
  sistema no puede verificar. Se usa `MARKED_SENT`, siempre presentado como autodeclaración.
- **Confirmar apertura real de WhatsApp mediante algún evento del navegador**: no existe tal evento fiable
  para una ventana de otro origen (`wa.me` redirige a `web.whatsapp.com` o al esquema `whatsapp://`); cualquier
  intento de inferirlo sería una señal poco confiable presentada como un hecho cierto.
- **Tabla de plantillas editable desde la UI**: rechazada en esta versión — mayor riesgo de que un usuario
  agregue una variable insegura sin la revisión que sí impone un cambio de código versionado.
- **Persistir `wa_url` en `communication_drafts`**: rechazada — embebería el texto completo del mensaje en una
  columna adicional redundante con `body`, sin ningún beneficio, y ampliaría la superficie de datos sensibles
  en reposo.

## Consecuencias
- Cualquier bloque futuro que agregue un segundo canal de comunicación asistida (ninguno planeado) debe repetir
  el mismo principio de estados honestos — nunca inventar un estado de confirmación que el sistema no pueda
  verificar realmente.
- La prueba `communicationTerminology.test.ts` actúa como barrera automática: cualquier cambio futuro de texto
  en las pantallas de comunicaciones que introduzca una de las frases prohibidas rompe la suite de pruebas.
- La UNIQUE auxiliar sobre `declaration_document_files` queda disponible para cualquier FK futura que necesite
  referenciar `(id, tenant_id, client_id)` de esa tabla, sin necesidad de agregarla de nuevo.
