# Política de seguridad — Luis IA Contable

Este proyecto administra información tributaria y personal sensible de contribuyentes peruanos. Las siguientes reglas son de cumplimiento obligatorio para cualquier persona o herramienta (incluyendo asistentes de IA) que contribuya código.

## Reglas invariables

1. **Ningún secreto en texto plano.** Contraseñas, tokens, claves API, credenciales SOL, refresh tokens: nunca en código fuente, JSON sin cifrar, logs, mensajes de error, capturas de pantalla, prompts enviados a modelos de IA, ni copias de seguridad sin cifrar.
2. **`.env` nunca se commitea.** Solo `.env.example` (sin valores reales) vive en el repositorio.
3. **Contraseñas de usuarios**: hash con Argon2id (`argon2-cffi`), nunca en texto plano ni con algoritmos débiles (MD5, SHA1, bcrypt con cost bajo).
4. **Credenciales SOL**: en esta fase (Fase 0/1) las columnas existen en el modelo de datos pero **no hay lógica de cifrado implementada**. Cualquier intento de escribir o leer un valor real debe fallar explícitamente (`NotImplementedError`), nunca simular una implementación real. La bóveda cifrada (AES-256-GCM + Argon2id + DPAPI) se implementa en Fase 4 — ver [docs/ROADMAP.md](docs/ROADMAP.md).
5. **Datos de prueba**: siempre ficticios. Nunca usar RUC, razones sociales, nombres, correos o teléfonos de contribuyentes reales en seeds, tests o documentación.
6. **No automatización de actos tributarios irreversibles**: el sistema nunca presenta declaraciones, realiza pagos, solicita fraccionamientos, modifica datos del RUC, da de alta/baja trabajadores, ni responde notificaciones de forma automática. Ver [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
7. **Aislamiento multi-tenant**: toda tabla con datos de negocio incluye `tenant_id`; ninguna consulta debe poder devolver datos de un tenant distinto al del usuario autenticado, incluso en la fase actual de un solo tenant.
8. **Principio de privilegio mínimo** en cualquier alcance OAuth (Google, etc.) que se integre en fases futuras: solicitar siempre el alcance más restringido posible.

## Reportar un problema de seguridad

Este es un proyecto de uso personal sin canal público de reporte. Si detectas una vulnerabilidad, documenta el hallazgo en `docs/DECISIONS/` como ADR o coméntalo directamente con el responsable del proyecto antes de continuar el desarrollo.

## Checklist antes de cada commit

- [ ] `git status` no muestra `.env` ni archivos con secretos
- [ ] Ningún dato de contribuyente real en el diff
- [ ] Ningún log o mensaje de error expone contraseñas, tokens o claves
- [ ] Las dependencias nuevas no introducen vulnerabilidades conocidas sin evaluar
