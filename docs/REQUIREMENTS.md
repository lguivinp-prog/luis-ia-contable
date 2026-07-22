# Requisitos — Luis IA Contable

Este documento distingue entre requisitos **implementados en esta entrega** (Fase 0 + Fase 1 bloque 1) y requisitos **de diseño para fases futuras**, que ya están contemplados en la arquitectura pero no tienen código todavía. Ver [ROADMAP.md](ROADMAP.md) para el detalle de fases.

## Requisitos funcionales — implementados en esta entrega

### RF-01 Autenticación
- El sistema debe permitir iniciar sesión con correo y contraseña de un usuario administrador.
- Las contraseñas se almacenan con hash Argon2id, nunca en texto plano.
- La sesión se mantiene mediante cookie `HttpOnly` con expiración configurable.
- El usuario administrador se crea únicamente mediante script de seed, no existe registro público.
- Cada intento de login (éxito o fallo) y cada cierre de sesión se audita.

### RF-02 Gestión de contribuyentes
- CRUD completo de contribuyentes (crear, listar, ver detalle, actualizar).
- No existe eliminación física; el ciclo de vida se controla mediante `service_status` (prospecto, activo, suspendido, pendiente_cierre, inactivo).
- El RUC se valida con el algoritmo oficial de dígito verificador (módulo 11) antes de aceptar el registro.
- El sistema calcula y almacena el último dígito del RUC (usado en fases futuras para cronogramas por último dígito).
- Cada contribuyente admite persona natural o jurídica, régimen tributario, datos de contacto, honorario mensual, etiquetas y observaciones.
- Toda creación, actualización y cambio de estado de un contribuyente se audita.

### RF-03 Consulta de RUC
- El sistema permite consultar datos públicos de un RUC a través de una interfaz desacoplada (`RucLookupProvider`).
- En esta entrega solo existe un proveedor simulado (`FakeRucLookupProvider`) con datos deterministas de prueba, marcados explícitamente como simulados (`is_simulated: true`).
- El proveedor real (SUNAT/terceros) está definido como interfaz pero no implementado; cualquier intento de uso lanza un error explícito, nunca datos falsos disfrazados de reales.
- El usuario puede ingresar los datos manualmente como alternativa a la consulta.

### RF-04 Catálogo de obligaciones tributarias
- Existe un catálogo configurable de obligaciones (Formulario 621, PLAME, SIRE-RVIE, SIRE-RCE, Renta Anual 710, ITAN, y otras) cargado mediante seed.
- Cada obligación registra su fuente normativa y versión de los datos (trazabilidad), sin fechas de cronograma inventadas.
- Cada contribuyente puede activar o desactivar obligaciones específicas de forma independiente.

### RF-05 Auditoría
- Se registra: inicios de sesión (éxito/fallo), cierre de sesión, creación/actualización/cambio de estado de contribuyentes, asignación/actualización/eliminación de obligaciones por cliente, y consultas de RUC.
- Los registros de auditoría nunca contienen el valor de un secreto.

## Requisitos no funcionales — implementados en esta entrega

- **RNF-01** Todos los importes monetarios se almacenan con tipo `NUMERIC`, nunca `float`.
- **RNF-02** Todas las fechas/horas se gestionan considerando la zona horaria `America/Lima`.
- **RNF-03** Toda tabla de negocio incluye `tenant_id`, preparando el aislamiento multi-tenant futuro.
- **RNF-04** El sistema debe arrancar completo (`db` + `backend` + `frontend`) mediante `docker compose up` en Windows 11 con Docker Desktop, sin requerir Node.js ni Python instalados en el host.
- **RNF-05** Ningún secreto se almacena en texto plano, en código fuente ni en `git`.
- **RNF-06** La interfaz debe ser responsive (uso desde celular en la misma red Wi-Fi, deshabilitado por defecto hasta configurar acceso seguro — ver [SECURITY_MODEL.md](SECURITY_MODEL.md)).
- **RNF-07** El código de dominio (validación de RUC, hashing, tokens) debe tener cobertura de pruebas unitarias.

## Requisitos de diseño para fases futuras (no implementados aún)

- Bóveda cifrada de credenciales SOL (Fase 4).
- Motor de cronogramas tributarios versionado con aprobación humana (Fase 1, bloques posteriores).
- Control de declaraciones, documentos, honorarios y notificaciones con flujo completo (Fase 1, bloques posteriores).
- Integración con Google Calendar, Google Drive y Gmail (Fases 2 y 3).
- Mensajería asistida por WhatsApp (Fase 2).
- Automatización supervisada del portal SOL con Playwright (Fase 4).
- SIRE, AFPnet, EsSalud, ITAN, Renta Anual, fraccionamientos (Fase 5).
- Multiusuario, roles, multidespacho y comercialización (Fase 6).

Estos requisitos están descritos en detalle en el encargo original del proyecto y se irán formalizando en este documento a medida que cada fase se implemente.
