# Manual de uso — Luis IA Contable

> Cubre únicamente las pantallas implementadas en el primer bloque de Fase 1 (verificadas en navegador). Se ampliará en cada fase siguiente.

## Inicio de sesión

Ingresa a http://localhost:3010, escribe tu correo y contraseña de administrador, y presiona "Ingresar". Si los datos son incorrectos, el sistema lo indica sin especificar cuál campo falló (por seguridad).

## Contribuyentes

- **Listado**: muestra todos los contribuyentes registrados, con filtros por estado de servicio y régimen tributario, y búsqueda por RUC o razón social.
- **Nuevo contribuyente**: formulario con los datos básicos (RUC, razón social, régimen, etc.). El RUC se valida automáticamente (formato y dígito verificador) antes de guardar. Puedes usar el botón "Consultar RUC (proveedor simulado)" para prellenar datos desde el proveedor simulado de desarrollo — los datos obtenidos así muestran un aviso indicando que son simulados y deben revisarse antes de guardar.
- **Detalle / edición**: permite actualizar los datos del contribuyente y cambiar su estado de servicio (prospecto, activo, suspendido, pendiente de cierre, inactivo) desde la sección "Cambiar estado de servicio", indicando siempre un motivo. Los contribuyentes nunca se eliminan físicamente.
- **Obligaciones del contribuyente**: página "Ver obligaciones" donde se asignan obligaciones del catálogo al contribuyente, y se activan, desactivan o quitan las ya asignadas.

## Catálogo de obligaciones

Lista de referencia (Formulario 621, PLAME, SIRE-RVIE, SIRE-RCE, Renta Anual 710, ITAN, etc.) cargada como dato de desarrollo. En esta fase es de solo lectura desde la interfaz; se asigna a cada contribuyente desde su ficha.

## Qué todavía no hace el sistema

Esta versión no gestiona vencimientos con fecha (el motor de cronogramas se implementa en un bloque posterior), ni honorarios, ni documentos, ni notificaciones, ni integraciones con Google/Gmail/WhatsApp. Ver [ROADMAP.md](ROADMAP.md).

## Cerrar sesión

Desde el menú superior, opción "Cerrar sesión". Esto revoca la sesión en el servidor de inmediato.
