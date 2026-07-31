# Instalación en Windows 11 — Luis IA Contable

> Pasos verificados de extremo a extremo (Fase 0 + primer bloque de Fase 1): `install.ps1` → `start.ps1` → `migrate.ps1` → `seed.ps1` → login real en el navegador → CRUD de contribuyentes → asignación de obligaciones → `backup.ps1` → `restore.ps1` → `reset-dev.ps1`.

## Requisitos previos

- Windows 11 Pro (64 bits).
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado, con el motor de Linux containers activo y en ejecución.
- PowerShell 5.1 (incluido en Windows) o PowerShell 7+.
- No es necesario instalar Node.js ni Python: todo el código corre dentro de contenedores Docker.

## Pasos

1. Clonar u obtener el repositorio en, por ejemplo, `C:\Users\<usuario>\Documents\luis-ia-contable`.
2. Abrir PowerShell en esa carpeta.
3. Ejecutar la instalación inicial:

   ```powershell
   .\scripts\install.ps1
   ```

   Este script verifica que Docker esté corriendo, crea el archivo `.env` a partir de `.env.example` (si no existe) y construye las imágenes. Revisa `.env` antes de continuar, en particular `DEV_SEED_ADMIN_EMAIL` / `DEV_SEED_ADMIN_PASSWORD` (credenciales del administrador ficticio que creará `seed.ps1`).

4. Iniciar el sistema:

   ```powershell
   .\scripts\start.ps1
   ```

   Espera a que los tres servicios (`db`, `backend`, `frontend`) queden en estado `healthy`.

5. Aplicar migraciones y cargar datos ficticios de desarrollo:

   ```powershell
   .\scripts\migrate.ps1
   .\scripts\seed.ps1
   ```

   Ambos scripts son idempotentes: puedes ejecutarlos varias veces sin duplicar datos.

6. Abrir en el navegador:
   - Frontend: http://localhost:3010
   - API / Swagger: http://localhost:8010/docs

7. Iniciar sesión con las credenciales del administrador ficticio definidas en tu archivo `.env` (`DEV_SEED_ADMIN_EMAIL` / `DEV_SEED_ADMIN_PASSWORD`).

## Uso diario (recomendado): un solo acceso directo

Una vez completada la instalación inicial (pasos 1-3 arriba), el día a día no requiere recordar ninguna
secuencia de comandos — ver ADR-0066. Crea el acceso directo una sola vez:

```powershell
.\scripts\create-desktop-shortcut.ps1
```

Esto crea "Luis IA Contable.lnk" en tu escritorio. A partir de ahí, un doble clic:

1. verifica que Docker esté disponible y en ejecución;
2. levanta los 3 servicios (`docker compose up -d`);
3. espera a que PostgreSQL, el backend y el frontend estén saludables (con un plazo máximo, nunca una espera
   indefinida);
4. aplica las migraciones pendientes (`alembic upgrade head`);
5. sincroniza la información local (tareas operativas generadas/actualizadas/resueltas desde declaraciones,
   documentos y honorarios — nunca integraciones externas, que todavía no existen o están diferidas);
6. abre `http://localhost:<FRONTEND_PORT>` en tu navegador por defecto.

Si algo falla, la ventana muestra un mensaje claro (nunca una traza técnica) y permanece abierta para que
puedas leerlo; el detalle completo queda en `logs\startup\startup-<fecha>.log`.

Para detener el sistema conservando todos los datos:

```powershell
.\scripts\stop-luis-ia-contable.ps1
```

### Inicio automático al iniciar sesión de Windows (opcional)

```powershell
.\scripts\install-autostart.ps1
```

Registra la tarea "Luis IA Contable - Inicio local" en el Programador de tareas de Windows, para el usuario
actual únicamente (no requiere guardar tu contraseña ni privilegios de administrador). Para desactivarlo:

```powershell
.\scripts\uninstall-autostart.ps1
```

El acceso directo manual sigue funcionando exactamente igual, este de arranque automático nunca es obligatorio
para poder usar el sistema.

## Detener el sistema (script original)

```powershell
.\scripts\stop.ps1
```

Esto detiene los contenedores sin borrar los datos de la base de datos (el volumen de Postgres persiste).
Equivalente a `stop-luis-ia-contable.ps1` de la sección anterior.

## Copias de seguridad

```powershell
.\scripts\backup.ps1
```

Genera un archivo `backups\backup_<fecha>_<hora>.sql` (con `--clean --if-exists`, para poder restaurarlo directamente sobre una base de datos ya existente).

Para restaurar (**acción destructiva**, sobrescribe los datos actuales; pide confirmación explícita escribiendo `RESTAURAR`):

```powershell
.\scripts\restore.ps1 -BackupFile "backups\backup_20260722_102802.sql"
```

## Reiniciar el entorno de desarrollo desde cero

```powershell
.\scripts\reset-dev.ps1
```

**Acción destructiva** (pide confirmación escribiendo `REINICIAR`): borra los volúmenes (incluida la base de datos), reconstruye, migra y vuelve a cargar los datos ficticios. Útil para volver a un estado limpio conocido.

## Arranque automático con Windows (Programador de tareas)

Ver "Uso diario (recomendado)" arriba: `scripts\install-autostart.ps1` / `scripts\uninstall-autostart.ps1`
(ADR-0066). Es opcional; el acceso directo manual (`scripts\create-desktop-shortcut.ps1`) funciona
exactamente igual sin instalarlo.

## Solución de problemas

- **Docker Desktop no está corriendo**: `install.ps1`/`start.ps1` lo detectan y muestran un mensaje indicando que debe iniciarse manualmente.
- **Puertos 3010/8010/5432 ocupados**: ajustar `BACKEND_PORT`, `FRONTEND_PORT`, `POSTGRES_PORT` en `.env` y reiniciar. Los valores por defecto de `.env.example` ya evitan los puertos más comunes (3000/8000) para reducir choques con otros proyectos en el mismo equipo.
- **Cambios en el modelo de datos no se reflejan**: correr `scripts/migrate.ps1` de nuevo.
- **Una página nueva del frontend responde 404 en desarrollo**: en algunos casos, el compilador Turbopack de Next.js no detecta de inmediato archivos de ruta nuevos creados fuera del propio contenedor (bind mount de Windows). Solución: `docker compose restart frontend`. Esto no ocurre con el build de producción (`npm run build`), solo en el modo desarrollo con recarga en caliente.
