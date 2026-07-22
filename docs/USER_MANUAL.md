# Manual de uso — Luis IA Contable

> Cubre las pantallas implementadas en Fase 1, Bloques 1 y 2 (verificadas en navegador). Se ampliará en cada fase siguiente.

## Inicio de sesión

Ingresa a http://localhost:3010, escribe tu correo y contraseña de administrador, y presiona "Ingresar". Si los datos son incorrectos, el sistema lo indica sin especificar cuál campo falló ni si la cuenta existe (por seguridad). Tras varios intentos fallidos consecutivos, la cuenta queda bloqueada temporalmente (nunca de forma permanente); el sistema indica cuánto falta para poder reintentar.

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

## Qué todavía no hace el sistema

Esta versión no genera declaraciones a partir de los cronogramas, ni gestiona honorarios, documentos, notificaciones, ni integraciones con Google/Gmail/WhatsApp/SUNAT Operaciones en Línea. Ver [ROADMAP.md](ROADMAP.md).

## Cerrar sesión

Desde el menú superior, opción "Cerrar sesión". Esto revoca la sesión en el servidor de inmediato.
