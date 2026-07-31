# Manual de uso — Luis IA Contable

> Cubre las pantallas implementadas en Fase 1, Bloques 1 a 5 (verificadas en navegador). Se ampliará en cada fase siguiente.

## Roles y permisos (Bloque 4)

El sistema tiene tres roles fijos: **Administrador** (acceso completo, incluida la gestión de usuarios y el
cierre excepcional de declaraciones), **Contador** (todo el trabajo operativo diario, sin gestión de usuarios
ni cierre excepcional) y **Asistente** (solo lectura en la mayoría de pantallas, más la capacidad de cambiar
el estado de documentos ya asignados, subir archivos y exportar reportes — sin poder reemplazar ni eliminar un
archivo ya subido). Los controles que un usuario no puede usar por su
rol quedan ocultos o deshabilitados en pantalla — si un control no aparece, es porque el rol actual no tiene
el permiso correspondiente, no un error.

## Inicio de sesión

Ingresa a http://localhost:3010, escribe tu correo y contraseña, y presiona "Ingresar". Si los datos son incorrectos, el sistema lo indica sin especificar cuál campo falló ni si la cuenta existe (por seguridad). Tras varios intentos fallidos consecutivos, la cuenta queda bloqueada temporalmente (nunca de forma permanente); el sistema indica cuánto falta para poder reintentar.

Si un administrador te asignó una contraseña temporal (alta de usuario o restablecimiento), el sistema te
lleva directamente a una pantalla de "Cambio de contraseña obligatorio" antes de mostrar cualquier otra
pantalla; no puedes navegar a otra parte de la aplicación hasta completarla.

## Sesión e inactividad

Mientras usas el sistema, una advertencia con cuenta regresiva aparece antes de que la sesión se cierre por inactividad, con la opción "Continuar sesión". Si no respondes, la sesión se cierra automáticamente. Desde "Seguridad" puedes ver tus sesiones activas y cerrar todas las demás sin afectar la sesión actual.

## Contribuyentes

- **Listado**: muestra todos los contribuyentes registrados, con filtros por estado de servicio y régimen tributario, y búsqueda por RUC o razón social.
- **Nuevo contribuyente**: formulario con los datos básicos (RUC, razón social, régimen, etc.). El RUC se valida automáticamente (formato y dígito verificador) antes de guardar. Puedes usar el botón "Consultar RUC (proveedor simulado)" para prellenar datos desde el proveedor simulado de desarrollo — los datos obtenidos así muestran un aviso indicando que son simulados y deben revisarse antes de guardar.
- **Detalle / edición**: permite actualizar los datos del contribuyente y cambiar su estado de servicio (prospecto, activo, suspendido, pendiente de cierre, inactivo) desde la sección "Cambiar estado de servicio", indicando siempre un motivo. Los contribuyentes nunca se eliminan físicamente.
- **Obligaciones del contribuyente**: página "Ver obligaciones" donde se asignan obligaciones del catálogo al contribuyente, y se activan, desactivan o quitan las ya asignadas.

## Catálogo de obligaciones

Lista de referencia (Formulario 621, PLAME, SIRE-RVIE, SIRE-RCE, Renta Anual 710, ITAN, etc.) cargada como dato de desarrollo. En esta fase es de solo lectura desde la interfaz; se asigna a cada contribuyente desde su ficha.

## Categorías tributarias (`/tax-categories`)

- **Catálogo**: lista y permite crear categorías (código, nombre, descripción) — p. ej. `BUEN_CONTRIBUYENTE`.
- **Por contribuyente**: busca un contribuyente por RUC o razón social, y asigna/quita categorías con su rango de vigencia (desde/hasta). El sistema impide registrar dos vigencias que se solapen para la misma categoría, pero sí permite renovaciones no solapadas. La columna "buen contribuyente" del listado de contribuyentes se calcula automáticamente a partir de estas asignaciones, a la fecha de hoy — nunca se edita directamente.

## Cronogramas tributarios (`/tax-schedules`)

> **Todo cronograma en este módulo es de demostración/ficticio.** El sistema lo recuerda en cada pantalla relevante; ninguna fecha aquí mostrada debe usarse para cumplimiento tributario real.

- **Fuentes**: cada fuente pertenece a una obligación del catálogo. Se crean desde la pantalla principal.
- **Importar CSV**: dentro de una fuente, sube un archivo CSV con las columnas requeridas (indicadas en pantalla), el año fiscal, el tipo de cronograma y, opcionalmente, una categoría de alcance. El sistema valida estructura, fechas, dígitos y duplicados antes de mostrar una vista previa con los errores encontrados o las diferencias respecto a la versión aprobada anterior.
- **Confirmar o rechazar la importación**: confirmar crea una nueva versión (aún no aprobada); rechazar descarta el intento, conservando el archivo original como evidencia.
- **Revisar y aprobar una versión**: desde el detalle de la versión, "Enviar a revisión" y luego "Aprobar" (requiere un comentario explícito) o "Rechazar"/"Cancelar" (requieren un motivo). Aprobar una versión reemplaza automáticamente la versión aprobada anterior del mismo alcance exacto (misma fuente, año, tipo y categoría) — nunca afecta otros años o categorías.
- **Comparar versiones**: desde el detalle de una versión, compárala con otra de la misma fuente para ver periodos agregados, eliminados o con fecha modificada.
- **Historial de aprobación** y **descarga de entradas en CSV** disponibles en el detalle de cada versión.
- **Vista previa de vencimiento**: selecciona un contribuyente, una fuente y un periodo tributario para ver qué regla se aplicó (excepción o entrada), el último dígito del RUC, la categoría exigida (si corresponde), la fecha resultante, si hubo prórroga, y advertencias. Esta pantalla **no genera declaraciones ni eventos de calendario**.

## Periodos tributarios (`/tax-periods`)

Lista y crea periodos canónicos (mensual/anual/especial) por año fiscal. Un periodo se cierra o reabre desde su detalle; **ninguna declaración de un periodo cerrado admite mutaciones** (workflow, presentación, documentos, determinación o pago de impuestos, cargos de honorarios nuevos) — el sistema lo indica explícitamente en cada pantalla afectada.

## Generar declaraciones (`/declarations/generate`)

Selecciona un periodo tributario abierto para previsualizar qué declaraciones se generarían (por cliente/obligación activa), excluir ítems puntuales, y confirmar la generación masiva. Si los datos subyacentes cambiaron entre la vista previa y la confirmación, el sistema detiene la operación como "desactualizada" (`STALE`) y exige refrescar la vista previa antes de confirmar — nunca genera declaraciones sobre datos obsoletos.

## Declaración — detalle (`/declarations/[id]`)

### Resumen
Vencimientos (inicial y vigente), versión de concurrencia, y el cambio de estado de flujo disponible según el estado actual (algunas regresiones piden un motivo explícito).

### Documentos
Lista los requisitos documentales ya materializados para esta declaración (nombre, si es general del cliente o vinculado a una obligación específica, estado, fechas, observaciones), el `document_status` derivado, y el botón **"Sincronizar requisitos"** — puede presionarse cuantas veces se quiera sin duplicar nada; muestra cuántos ítems se crearon y cuántos ya existían. Cada ítem tiene un selector de nuevo estado (solicitado, recibido, parcial, observado, aceptado, no aplica, etc.) con un botón "Aplicar".

**Sincronización administrativa por periodo (declaraciones generadas antes de existir control documental):** si una declaración ya existente nunca recibió requisitos porque se generó antes de asignarlos al cliente, no hace falta ninguna migración — basta con asignar el requisito al cliente y luego presionar "Sincronizar requisitos" en la declaración (o, para todo un periodo a la vez, un administrador puede invocar `POST /api/v1/tax-periods/{id}/documents/sync-requirements`, que recorre todas las declaraciones del periodo sin afectar el resto si una falla individualmente).

**Archivos por ítem (Bloque 5)**: cada requisito tiene su propia columna "Archivos". Cualquier rol con permiso de escritura documental (incluido asistente) puede subir un archivo nuevo (PDF, JPG, PNG, DOCX o XLSX, hasta 15 MB) — sube como una cadena de versiones independiente; un ítem puede tener varias cadenas simultáneas (p. ej. varios comprobantes distintos para el mismo requisito). Cada cadena muestra su versión vigente con estado "Activo" y botones "Descargar" (los 3 roles), y — solo para administrador/contador — "Reemplazar" (sube una nueva versión indicando un motivo obligatorio; la versión anterior pasa a "Reemplazado" y queda visible en el historial colapsable) y "Eliminar" (pide motivo y confirmación reforzada; el archivo físico se conserva para auditoría, pero la cadena queda cerrada — un documento nuevo para el mismo requisito es una cadena nueva, no una reactivación). Subir o reemplazar un archivo **nunca** cambia automáticamente el estado del ítem (`NOT_REQUESTED`/`REQUESTED`/etc.) — eso sigue siendo una acción manual separada con el selector de estado. Un archivo cuyo contenido no corresponde a un formato permitido (incluido un archivo renombrado para simular otra extensión) es rechazado con un mensaje explícito.

### Impuestos
**Determinación tributaria**: si la declaración no tiene determinación, un formulario permite registrar la primera (impuesto bruto + créditos aplicados); el sistema calcula automáticamente el total por pagar y el saldo a favor — nunca se ingresan directamente. Una vez determinada, el mismo formulario se convierte en "Registrar corrección", que exige un motivo explícito y una confirmación adicional antes de enviarse; el historial completo de determinaciones queda visible debajo.

**Pagos tributarios**: muestra el saldo tributario actual y permite registrar un pago (monto, fecha, método, número de operación, referencia). Si el pago excede el saldo pendiente, el sistema pide confirmar explícitamente el sobrepago e indicar el motivo antes de reintentar. Cada pago listado muestra qué determinación estaba vigente cuando se registró, y puede anularse indicando un motivo (nunca se elimina). Un reenvío accidental del mismo formulario (doble clic) nunca duplica el pago.

### Cierre
Muestra las tres precondiciones del cierre normal (presentación, documentos, impuesto) con su estado. El botón de cierre normal solo se habilita si las tres están cumplidas y el usuario tiene permiso para cerrar declaraciones (administrador o contador). Si falta alguna precondición y el usuario es administrador, aparece la opción "Cerrar con pendientes (excepcional)", que exige un motivo explícito y una confirmación reforzada; una vez cerrada, la declaración muestra visiblemente si el cierre fue normal o con pendientes.

### Historial
Registro completo de cambios de estado y de vencimiento con motivo, actor y fecha.

## Honorarios profesionales (`/clients/[id]/fees`)

Desde la ficha del contribuyente, botón "Honorarios":

- **Acuerdos**: crea un acuerdo por tipo de servicio y monto, con vigencia desde/hasta. Un acuerdo activo puede **superarse** (renegociar la tarifa) o **cancelarse**, ambas acciones piden motivo y cierran realmente la vigencia — permitiendo crear inmediatamente un acuerdo nuevo no solapado del mismo tipo de servicio.
- **Cargos**: dentro de un acuerdo, genera un cargo para un periodo tributario (monto opcional distinto al del acuerdo, vencimiento opcional). Generar el mismo cargo dos veces para el mismo periodo es rechazado. Un cargo se **emite** indicando su fecha de vencimiento (o dejándola vacía si no aplica seguimiento de cobranza).
- **Pagos y recibos**: dentro del detalle de un cargo emitido, se registran pagos (con las mismas reglas de sobrepago/anulación que los pagos tributarios) y se emiten recibos (pueden ser parciales, pero su suma nunca puede exceder el monto del cargo). El **riesgo de cobranza** (en curso / vence hoy / vencido / pagado / cancelado) se muestra en vivo, calculado a partir de la fecha de vencimiento y el saldo actual — no es un valor guardado, cambia automáticamente con el paso de los días.

## Requisitos documentales del cliente (`/clients/[id]/documents`)

Crea tipos de documento personalizados para el tenant y los asigna al cliente (general o vinculado a una obligación concreta), con periodicidad y vigencia.

## Tablero mensual (`/dashboard`)

Una fila por declaración (nunca se duplica aunque tenga varios documentos, pagos o cargos de honorarios). Columnas: flujo, presentación, documentos (con conteo de faltantes), impuesto (determinado/pagado), honorarios (cargos, saldo, peor riesgo de cobranza), riesgo general de la declaración, y si el cierre fue normal o con pendientes. Filtros por periodo, estado tributario, estado documental y cierre con pendientes.

## Reportes (`/reports`)

Exporta datos del tenant (declaraciones, documentos, determinaciones, pagos tributarios, acuerdos/cargos/pagos/recibos de honorarios, contribuyentes, tareas operativas, registro de auditoría) en CSV o Excel. Cada exportación queda registrada en el historial de auditoría; el número de filas exportables tiene un límite configurable por el equipo técnico.

## Tareas (`/tasks`) y Agenda (`/agenda`)

Visible para los 3 roles (Administrador, Contador, Asistente).

- **Lista de tareas** (`/tasks`): filtra por estado, prioridad, balde de vencimiento (Vencidas/Vencen hoy/
  Próximos 7 días) y responsable (Asignadas a mí/Sin responsable). El botón "Sincronizar" genera de forma
  idempotente las tareas automáticas pendientes (presentación de declaraciones, recopilación de documentos
  solicitados/observados, cobranza de honorarios vencidos) — ejecutarlo varias veces nunca duplica tareas.
- **Nueva tarea** (`/tasks/new`): título, descripción, tipo, prioridad, fecha/hora de vencimiento y un
  selector opcional de cliente ("Sin cliente" o uno de los clientes del tenant). Una tarea manual nunca se
  vincula directamente a una declaración/documento/cobranza específica — eso solo lo hace la sincronización
  automática.
- **Detalle de tarea** (`/tasks/[id]`): contenido, historial completo (quién/cuándo/qué cambió, incluidas las
  mutaciones automáticas de la sincronización), comentarios, y acciones (iniciar, completar, cancelar con
  motivo, reprogramar, reasignar). Una tarea generada automáticamente que se marca completada/cancelada por
  la propia sincronización (porque su fuente ya no requiere trabajo) queda visible como resuelta
  automáticamente en el historial.
- **Autorización del Asistente**: puede ver todas las tareas del despacho, pero solo puede iniciar/completar/
  reprogramar/comentar las que tiene asignadas a sí mismo — sobre cualquier otra, los botones aparecen
  deshabilitados (no ocultos) con un mensaje explicando por qué. No puede reasignar ni cancelar ninguna
  tarea, ni asignar una tarea nueva a otra persona (solo a sí mismo o sin responsable).
- **Agenda** (`/agenda`): vista agrupada por Vencidas/Vencen hoy/Próximos 7 días, con los mismos conteos que
  las tarjetas del tablero mensual — nunca un cálculo distinto entre ambas pantallas.
- **Tablero mensual**: incluye tarjetas de tareas (Vencidas, Vencen hoy, Próximos 7 días, Urgentes, Sin
  responsable, Asignadas a mí) que enlazan directamente a la lista de tareas ya filtrada.

## Gestión de usuarios (`/users`, solo Administrador)

Visible únicamente para el rol Administrador (el enlace "Usuarios" no aparece para Contador ni Asistente).

- **Listado**: correo, nombre, rol, estado (activo/inactivo), si tiene una contraseña temporal pendiente de
  cambio, y último inicio de sesión.
- **Nuevo usuario**: correo, nombre completo, rol y una contraseña inicial (temporal) que el propio
  administrador define. El usuario nuevo deberá cambiarla en su primer inicio de sesión.
- **Cambiar rol**: exige un motivo explícito. No puedes cambiar tu propio rol, ni el sistema permite dejar a
  un tenant sin ningún administrador activo (si eres el único, la operación se rechaza).
- **Desactivar**: exige un motivo; revoca de inmediato todas las sesiones activas del usuario (aunque tenga
  una pestaña abierta, su siguiente acción falla). No puedes desactivarte a ti mismo, ni desactivar al último
  administrador activo del tenant.
- **Reactivar**: sin motivo requerido; el usuario puede volver a iniciar sesión normalmente.
- **Restablecer contraseña**: exige un motivo; define una nueva contraseña temporal, revoca todas las
  sesiones del usuario objetivo, y lo obliga a cambiarla en su próximo inicio de sesión.

## Integración con Google (`/settings/integrations/google`) — Fase 3, Bloque 3.1

**Estado actual: diferida (`GOOGLE_LIVE_VALIDATION_DEFERRED`).** Mientras el despacho no registre un proyecto
de Google Cloud con credenciales OAuth reales (`GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` en
`.env`), la pantalla muestra "Google no configurado en este entorno" y no ofrece el botón de conexión — no
hay ningún botón que termine en un error. Esto **no afecta ninguna otra función del sistema**: tareas,
declaraciones, honorarios, documentos y agenda funcionan con total normalidad sin Google configurado. Una vez
que el despacho registre esas credenciales, la integración queda disponible sin necesidad de ningún cambio de
código.

Visible para quien tenga el permiso de integraciones (los 3 roles en este bloque). Permite conectar tu propia
cuenta de Google **únicamente para verificar tu identidad** (correo verificado) — todavía no sincroniza
Calendar ni Drive, eso llega en bloques posteriores de la Fase 3. Al conectar, se te redirige a Google para
autorizar; al volver, la pantalla muestra el correo de la cuenta conectada y el estado. "Desconectar" pide
confirmación explícita y revoca el acceso en Google en la medida de lo posible (si la revocación remota falla,
la conexión igual queda desconectada localmente). Si la conexión requiere reautorización (por ejemplo, tras
una revocación desde tu cuenta de Google), la pantalla lo indica y ofrece "Reconectar". Nadie más en el
despacho ve ni puede operar tu conexión — es individual por usuario.

**Nota operativa**: si el proyecto de Google Cloud del despacho está en modo "Testing" (no verificado), esto
no afecta a esta conexión de solo identidad, pero sí afectará más adelante a Calendar/Drive (autorizaciones
que expiran a los 7 días hasta que el proyecto se verifique).

## Qué todavía no hace el sistema

Esta versión no envía notificaciones por correo/SMS/push (las tareas y alertas solo se ven dentro de la aplicación), ni sincroniza con Google Calendar/Drive, ni tiene integraciones con Gmail/WhatsApp/SUNAT Operaciones en Línea, ni presenta declaraciones o realiza pagos reales. Desde Bloque 5 los documentos sí se almacenan como archivos reales (PDF/JPG/PNG/DOCX/XLSX, con reemplazo versionado y eliminación lógica); lo que no existe todavía es restauración de una versión eliminada, previsualización embebida de Office, ni purga física automática de archivos eliminados. Las rectificatorias tributarias quedan excluidas del producto por decisión funcional del propietario (no reaparecen como pendiente). Ver [ROADMAP.md](ROADMAP.md).

## Cerrar sesión

Desde el menú superior, opción "Cerrar sesión". Esto revoca la sesión en el servidor de inmediato.
