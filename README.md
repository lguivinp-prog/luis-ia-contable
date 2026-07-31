# Luis IA Contable

Aplicación peruana de control contable, tributario, documental y de honorarios profesionales para un contador independiente. Arquitectura local-first (Windows 11 + Docker), preparada para evolucionar a una plataforma multiusuario y multidespacho.

> **Estado actual:** Fase 0 (fundamentos) + Fase 1, Bloques 1 y 2 (núcleo operativo: autenticación endurecida con bloqueo de intentos fallidos y CSRF, contribuyentes, catálogo de obligaciones, categorías tributarias del cliente, motor de cronogramas tributarios versionado). Ver [docs/ROADMAP.md](docs/ROADMAP.md) para el detalle de fases.

## Qué es y qué NO es

Luis IA Contable es un sistema de **control, seguimiento, alertas, documentos y reportes**. **No presenta declaraciones ni realiza pagos tributarios de forma automática**, y nunca ejecuta acciones irreversibles ante SUNAT u otras entidades sin confirmación humana explícita. Ver [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md) y [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Frontend | Next.js 16 (App Router, Turbopack), TypeScript estricto, React 19, PWA (service worker manual) |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (síncrono), Alembic |
| Base de datos | PostgreSQL 16 |
| Infraestructura | Docker Compose, scripts PowerShell para Windows 11 |

Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para el detalle y versiones exactas.

## Requisitos previos

- Windows 11 Pro (64 bits)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y en ejecución
- PowerShell 5.1+ o PowerShell 7+

No es necesario instalar Node.js ni Python en el equipo: todo corre dentro de contenedores Docker.

## Puesta en marcha rápida

```powershell
.\scripts\install.ps1
.\scripts\start.ps1
.\scripts\migrate.ps1
.\scripts\seed.ps1
```

- Frontend: http://localhost:3010
- API (Swagger/OpenAPI): http://localhost:8010/docs
- Credenciales del administrador ficticio: ver variables `DEV_SEED_ADMIN_EMAIL` / `DEV_SEED_ADMIN_PASSWORD` en tu archivo `.env` local.

Para detener el sistema:

```powershell
.\scripts\stop.ps1
```

Instrucciones completas y solución de problemas en [docs/WINDOWS_INSTALLATION.md](docs/WINDOWS_INSTALLATION.md).

### Uso diario: un solo acceso directo (ver ADR-0066)

Tras la instalación inicial, `scripts\create-desktop-shortcut.ps1` crea un acceso directo que levanta los
servicios, aplica migraciones, sincroniza la información local y abre el navegador con un solo doble clic —
opcionalmente también al iniciar sesión de Windows (`scripts\install-autostart.ps1`). Ver
"Uso diario" en [docs/WINDOWS_INSTALLATION.md](docs/WINDOWS_INSTALLATION.md).

## Scripts disponibles

| Script | Propósito |
|---|---|
| `scripts/install.ps1` | Primera instalación: crea `.env`, construye imágenes Docker |
| `scripts/start.ps1` / `stop.ps1` / `restart.ps1` | Encender / apagar / reiniciar el stack |
| `scripts/start-luis-ia-contable.ps1` / `stop-luis-ia-contable.ps1` | Orquestador de uso diario: levanta servicios, migra, sincroniza información local y abre el navegador / detiene conservando los datos (ADR-0066) |
| `scripts/create-desktop-shortcut.ps1` | Crea el acceso directo de escritorio para el orquestador de arranque |
| `scripts/install-autostart.ps1` / `uninstall-autostart.ps1` | Activa/desactiva el arranque automático al iniciar sesión de Windows (opcional) |
| `scripts/migrate.ps1` | Aplica migraciones de base de datos (Alembic) |
| `scripts/seed.ps1` | Carga datos ficticios de desarrollo (idempotente) |
| `scripts/backup.ps1` / `restore.ps1` | Copia de seguridad y restauración de la base de datos |
| `scripts/test.ps1` | Corre linters y pruebas automatizadas (backend y frontend) |
| `scripts/reset-dev.ps1` | Reinicia el entorno de desarrollo desde cero (destructivo, pide confirmación) |
| `scripts/logs.ps1` | Muestra logs de los servicios |
| `scripts/unlock-login.ps1` | Desbloqueo administrativo de una cuenta bloqueada por intentos fallidos (ver ADR-0009) |

## Documentación

Toda la documentación funcional, técnica y de seguridad vive en [docs/](docs/):

- [PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) — resumen ejecutivo del proyecto
- [REQUIREMENTS.md](docs/REQUIREMENTS.md) — requisitos funcionales y no funcionales
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — arquitectura técnica
- [DATA_MODEL.md](docs/DATA_MODEL.md) — modelo de datos
- [SECURITY_MODEL.md](docs/SECURITY_MODEL.md) / [THREAT_MODEL.md](docs/THREAT_MODEL.md) — seguridad y amenazas
- [TAX_RULES_MODEL.md](docs/TAX_RULES_MODEL.md) — cómo se gestionan las reglas tributarias (versionadas, nunca hardcodeadas)
- [ROADMAP.md](docs/ROADMAP.md) — fases de desarrollo
- [TEST_PLAN.md](docs/TEST_PLAN.md) — estrategia de pruebas
- [USER_MANUAL.md](docs/USER_MANUAL.md) — manual de uso
- [DECISIONS/](docs/DECISIONS/) — registro de decisiones arquitectónicas (ADRs)

## Seguridad

Ver [SECURITY.md](SECURITY.md). Este proyecto maneja información tributaria sensible: nunca se deben commitear secretos, credenciales reales ni datos de contribuyentes reales en este repositorio.

## Licencia y alcance

Proyecto de uso personal para un contador independiente. No sustituye el criterio profesional del contador ni actúa como asesor tributario automatizado.
