# Modelo de amenazas — Luis IA Contable

Metodología informal basada en STRIDE, adaptada a una aplicación local-first de un solo usuario con datos tributarios sensibles.

| # | Amenaza | Vector | Mitigación actual | Mitigación futura |
|---|---|---|---|---|
| 1 | Robo del equipo | Acceso físico a la laptop/PC | BitLocker del sistema operativo (fuera del alcance de la app), sesión de aplicación expira | Bloqueo automático por inactividad, bóveda separada con contraseña maestra (Fase 4) |
| 2 | Malware en el equipo | Keylogger, exfiltración de archivos | N/A en esta fase | Cifrado de credenciales SOL en reposo (Fase 4) limita el daño incluso con acceso al disco |
| 3 | Acceso no autorizado por red local | Otro dispositivo en la misma Wi-Fi | Acceso LAN deshabilitado por defecto | Autenticación + HTTPS local + firewall de red privada antes de habilitar (Fase 2+) |
| 4 | Filtración de credenciales SOL | Backup sin cifrar, log accidental | Columnas preparadas pero sin lógica funcional todavía (no hay secreto real que filtrar) | AES-256-GCM + Argon2id + DPAPI, nunca en logs (Fase 4) |
| 5 | Exposición mediante logs | Log de request/response completo | Logging configurado para no volcar cuerpo de requests de auth ni columnas sensibles | Auditoría de qué campos se loguean como parte de cada PR |
| 6 | Copias de seguridad inseguras | Backup subido a Drive sin cifrar | `scripts/backup.ps1` genera dump local en `backups/` (gitignored) | Cifrado antes de subir a Drive (Fase 2) |
| 7 | Tokens OAuth robados | Almacenamiento inseguro de refresh tokens | N/A (sin integraciones OAuth todavía) | Igual tratamiento que credenciales SOL cuando se implemente Google (Fase 2) |
| 8 | Sesiones SOL abiertas sin supervisión | Automatización de navegador sin control humano | N/A (Playwright no implementado) | Flujo supervisado obligatorio, detención ante CAPTCHA (Fase 4) |
| 9 | Errores de asociación de RUC | Confundir contribuyentes al importar/consultar | Unicidad `(tenant_id, ruc)`, validación de dígito verificador antes de guardar | Confirmación humana antes de sobrescribir datos de una consulta externa (Fase 1, bloques posteriores) |
| 10 | Fuga entre tenants | Query sin filtro de `tenant_id` | Todas las tablas de negocio incluyen `tenant_id`; repositorios siempre filtran por él | Pruebas de integración específicas de aislamiento multi-tenant al introducir el segundo tenant (Fase 6) |
| 11 | Prompt injection vía documentos/correos | Contenido malicioso en PDFs/correos instruyendo a un modelo de IA | N/A (sin integración de IA todavía) | Todo contenido externo se trata como dato no confiable, nunca como instrucción (Fase 3+) |
| 12 | Automatizaciones duplicadas | Reintentos de jobs programados creando eventos/registros repetidos | N/A (sin scheduler todavía) | Identificadores externos + verificación de duplicados antes de crear (Fase 2+) |
| 13 | Cambios en páginas oficiales rompen scraping | SUNAT cambia su portal | N/A (sin scraping todavía) | Selectores versionados y aislados en adaptadores (Fase 4) |
| 14 | Pérdida de conectividad / apagado inesperado | Corte de luz, hibernación | Docker Compose reinicia servicios; Postgres con volumen persistente | Recuperación de jobs omitidos al reiniciar (Fase 2+) |
| 15 | Inyección SQL | Entrada de usuario mal validada | SQLAlchemy con consultas parametrizadas, Pydantic valida todo input | Revisión de seguridad periódica del código |
| 16 | Fuerza bruta de login | Intentos repetidos de contraseña | Auditoría de `LOGIN_FAILURE` | Bloqueo por intentos fallidos (siguiente bloque de Fase 1) |

## Fuera de alcance en esta entrega

No existen todavía: bóveda de credenciales real, integraciones OAuth, automatización de navegador, scheduler de jobs. Este documento se actualiza en cada fase a medida que se activan las mitigaciones correspondientes.
