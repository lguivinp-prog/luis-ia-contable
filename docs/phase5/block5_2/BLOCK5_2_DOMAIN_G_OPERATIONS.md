# Bloque 5.2 — Domain G: empaquetado, instalación, arranque, recuperación y auditoría final

Alcance del producto (sin cambios): **`AUTHENTICATE_TO_LANDING_ONLY`**. Sin Buzón, mensajes, descargas, declaraciones,
rectificaciones, pagos ni automatización posterior al menú SOL. Antes de autorizar CUALQUIER automatización posterior al
aterrizaje se exige un marcador estructural positivo de shell autenticado, validado en vivo (regla congelada en E3).

Decisión de arquitectura: [ADR-0073](../../DECISIONS/ADR-0073-sol-agent-packaging-and-local-deployment.md).

## 1. Modelo de componentes (preservado)

```
Frontend (Next.js) -> Backend API  +  broker local (MISMO proceso, loopback, mTLS)
                                          |  mTLS (127.0.0.1:8995 pairing / 8996 operacional)
                                  Agente Windows NATIVO (aplicación interactiva por usuario)
                                          -> Chromium visible (Playwright) -> login SOL -> SOLO aterrizaje
```

- El Agente **no es un servicio de Windows** (`WINDOWS_AGENT_RUNS_AS_WINDOWS_SERVICE=NO`): necesita Chromium visible y
  supervisión humana en el escritorio interactivo (sesión de usuario, no Session 0).
- **Backend + broker corren como proceso NATIVO del usuario** (decisión del responsable): el broker acepta únicamente
  loopback y un Agente nativo no puede alcanzar un broker dentro de un contenedor sin relajar esa regla. El PostgreSQL
  puede seguir en Docker (puerto publicado). El `docker-compose.yml` de desarrollo **no cambia** y mantiene
  `SOL_BROKER_ENABLED=false`.

## 2. Disposición en disco

| Ruta | Contenido | Upgrade |
|---|---|---|
| `%LOCALAPPDATA%\LuisIAContable\SolAgentApp\app\` | `luis-sol-agent.exe` (consola), `luis-sol-agent-w.exe` (sin consola, autoarranque), `_internal\`, `build_info.json` | se reemplaza |
| `%LOCALAPPDATA%\LuisIAContable\SolAgentApp\browsers\chromium-<rev>\` | Chromium de Playwright propiedad de la app (solo el motor) | se añade si la revisión es nueva |
| `%LOCALAPPDATA%\LuisIAContable\SolAgent\` | identidad (`agent.cert.pem`, `agent.key.enc.pem` PKCS#8 cifrado, `agent.keypass.dpapi`, `identity.json`), `broker_ca\ca.cert.pem` (solo público), `logs\agent.log` | **nunca se toca** |

Estado autenticado del navegador: **efímero** (BrowserContext sin perfil persistente, sin `storageState`, sin trazas,
video ni capturas). En la build empaquetada `PLAYWRIGHT_BROWSERS_PATH` se fija siempre al directorio propio (se ignora
cualquier valor ambiente y `.pw-browsers-win-e2`). Chromium de esta build: revisión **1243**, `153.0.8010.12`.

## 3. Prerrequisitos

- Windows 11 x64; PowerShell 5.1.
- Backend nativo: Python 3.13.x **de python.org** (no la versión de Microsoft Store: ver §12) con
  `pip install -r backend/requirements.txt` en un venv (`.venv-win-backend`).
- Build del Agente: venv dedicado `.venv-win-pkg` (Python **3.13.14**) con `packaging/windows-agent/requirements-packaging.txt`
  (PyInstaller 6.22.3, hooks-contrib 2026.8, cryptography 43.0.3, httpx 0.28.1, playwright 1.63.0 — solo desarrollo) y un
  directorio con Chromium de Playwright (`playwright install chromium`, p. ej. `.pw-browsers-win-e2`).
- PostgreSQL accesible desde el backend.

## 4. Aprovisionamiento EXPLÍCITO de la CA del broker

El arranque del backend **nunca autogenera la CA**. Es un paso intencional previo:

```powershell
deploy\windows-local\provision-broker-ca.ps1 -CaDir C:\LuisIAContable\sol_broker_pki -ExportPublicTo C:\LuisIAContable\public_ca
```

- Se niega si la CA ya existe (rotarla invalida a todos los agentes emparejados) y si `-CaDir` está dentro del repositorio.
- Restringe el directorio al usuario actual (`icacls`). Imprime solo la huella SHA-256 del certificado público.
- **Almacenamiento de la clave privada de la CA**: `ca.key.pem` es PKCS#8 **sin cifrar** en disco (diseño de Domain D),
  protegido únicamente por ACL del sistema de archivos / volumen; **no** está en el repo, ni en una imagen Docker, ni en
  logs, ni en PostgreSQL. Riesgo residual aceptado y documentado: cualquier proceso del mismo usuario puede leerla.
  Endurecer (p. ej. DPAPI/HSM) sería un rediseño de Domain D, fuera de G.
- El Agente solo necesita el certificado **público**: `manage.ps1 install -CaCertificate ...` / `set-ca` lo copian a
  `SolAgent\broker_ca\ca.cert.pem` y **rechazan cualquier archivo con una clave privada**.

## 5. Habilitar el broker y arrancar el backend

```powershell
copy deploy\windows-local\sol-local.env.example deploy\windows-local\sol-local.env   # completar secretos propios
deploy\windows-local\start-backend.ps1 -Migrate -Background          # alembic upgrade head + uvicorn 127.0.0.1:8000, 1 worker
deploy\windows-local\stop-backend.ps1                                # mata el árbol de procesos
```

- `sol-local.env` activa `SOL_BROKER_ENABLED=true`, `SOL_BROKER_HOST=127.0.0.1`, `BACKEND_WORKERS=1` (la bóveda vive en la
  memoria de un único proceso). `start-backend.ps1` aborta con `BROKER_CA_MATERIAL_MISSING` si falta la CA.
- Logs de backend (`-Background`): un archivo por arranque en `-LogDir` con retención acotada (`-KeepLogs`, por defecto 10).
- Diagnóstico del lado backend (solo hechos no secretos): `python -m app.broker.diagnostics`.

## 6. Build del paquete

```powershell
packaging\windows-agent\build.ps1 [-Version 0.1.0-alpha.1]
```

Salida (ignorada por git) en `packaging\windows-agent\dist\`: carpeta `luis-sol-agent-<ver>-win-x64\` (`app\`, `browsers\`,
`scripts\manage.ps1`, `build_info.json`, `MANIFEST.sha256`) y el `.zip` con su `.sha256`. PyInstaller **one-folder** (no
one-file). `build_info.json` registra versión/build id, commit, arquitectura, Python, PyInstaller, Playwright, Chromium y
`signed=false`. El paquete **no contiene** venvs de desarrollo, pytest, fixtures, credenciales, identidades, material de
CA ni artefactos de pruebas (verificado).

## 7. Instalación, emparejamiento y autoarranque

```powershell
packaging\windows-agent\manage.ps1 install -PackageDir <carpeta> -CaCertificate C:\LuisIAContable\public_ca\ca.cert.pem [-Start]
python -m app.broker.pair_cli --tenant-id <UUID> --user-id <UUID>      # operador: muestra pairing_id + código (una vez)
"<SolAgentApp>\app\luis-sol-agent.exe" pair                            # ingresa pairing_id y el código (oculto)
```

- `install` verifica `MANIFEST.sha256` (archivo por archivo) antes de copiar.
- Autoarranque: **tarea de Task Scheduler** `\LuisIAContable\SolAgent`: disparador *al iniciar sesión* del usuario actual,
  `LogonType Interactive`, `RunLevel Limited`, `MultipleInstances IgnoreNew`, reinicio acotado (3 × 1 min), sin límite de
  ejecución. Argumento: `run --enable-sol-auth` (solo rutas; nunca secretos). `-DisableSolAuth` registra `run` sin
  autenticación (útil para pruebas sin SUNAT).
- El producto aún no expone un endpoint de usuario para crear pairings (`sol_agent:manage` reservado); `pair_cli` usa la
  misma autoridad de servicio (rate limit, verificador HMAC, TTL).
- Instancia única: mutex nombrado `LuisIAContable_SOL_Agent_SingleInstance_v1`; un segundo `run` termina con código 3.

## 8. Operación

| Acción | Comando |
|---|---|
| Estado | `manage.ps1 status [-BackendUrl http://127.0.0.1:8000]` (instalación, tarea, proceso/sesión, CA, logs, backend, puertos del broker, `exe status`) |
| Iniciar / detener | `manage.ps1 start` (dispara la misma tarea de autoarranque) / `manage.ps1 stop` |
| Actualizar (manual) | `manage.ps1 upgrade -PackageDir <nueva carpeta>`: verifica manifiesto, detiene, conserva `app.previous` (rollback automático si falla), **no toca la identidad**, reinicia |
| Desinstalar | `manage.ps1 uninstall`: detiene, quita la tarea y los binarios; **conserva la identidad** |
| Purga / reset | `manage.ps1 purge -ConfirmPurge DELETE-AGENT-IDENTITY` (Agente detenido): única operación que borra la identidad; requiere re-emparejar y revocar el agente en el backend |
| Reponer CA pública | `manage.ps1 set-ca -CaCertificate ...` (tras una purga) |
| Diagnóstico | `luis-sol-agent.exe version` / `status` / `browser-check` (abre y cierra Chromium en `about:blank`) |

`-Root <ruta>` permite instalar fuera de `%LOCALAPPDATA%\LuisIAContable` (el exe recibe `--data-dir`); se usó en las pruebas.
No hay actualización automática (`AUTO_UPDATE_IMPLEMENTED=NO`): upgrade manual verificado por SHA-256.

## 9. Recuperación

- **Backend/broker caído**: el Agente sigue vivo, reintenta con backoff acotado y reconecta con un HELLO nuevo (connection_id
  nuevo) al volver el broker; la UI vuelve a ver al Agente disponible. Un Agente matado sin desconexión limpia deja de
  figurar disponible pasado el umbral `sol_agent_online_threshold_seconds` (hallazgo de G: la disponibilidad exige además un
  latido reciente; el registro de conexiones solo sabe que hubo HELLO).
- **Contexto de autorización humana de F** (en memoria del proceso backend): un reinicio lo pierde; no se reconstruye
  desde BD, no se recrea ningún grant y no hay entrega de credencial ni reentrega automática. Estado observado: la sesión
  queda `IN_PROGRESS` sin motivo hasta que el usuario la cancele o venza; `PREAUTH_READY` y el pedido de credencial reciben
  409. Para reintentar: cancelar (si sigue activa) y **iniciar una sesión supervisada nueva** (y desbloquear la bóveda).
- **Agente matado**: el mutex lo libera Windows; al reiniciar (tarea) conserva la identidad, no re-reclama ni re-ejecuta el
  job anterior y no hay reentrega de secretos.
- **Identidad corrupta**: el Agente falla cerrado (`IDENTITY_CORRUPT`); nunca se borra ni se re-empareja automáticamente.

## 10. Logs

- Agente: `SolAgent\logs\agent.log`, rotativo (2 MiB × 5), solo eventos de ciclo de vida con identificadores no sensibles
  (sin usuario/clave SOL, frase DPAPI, clave privada, cuerpos de respuesta de credencial, cookies, contenido del desafío).
- Backend: ver §5. Nunca se registran secretos de bóveda ni credenciales.

## 11. Limitaciones conocidas y advertencias

- **Build sin firma** (`WINDOWS_BINARY_SIGNED=NO`): no hay certificado de firma de código de confianza disponible. Esperar
  avisos de SmartScreen/antivirus; verificar **siempre** el SHA-256 del `.zip` y el `MANIFEST.sha256`. La firma de código es
  un requisito de endurecimiento previo a un release.
- **`NEEDS_VALIDATION`** no significa "inutilizable": es elegible para la autenticación supervisada. La transición a `ACTIVE`
  no está definida (ADR-0068) y no se implementa; un acceso exitoso NO persiste validación `ACTIVE`.
- El contexto JIT de F es local al proceso (ver §9); el backend debe ser un único proceso.
- Reinicio real de la máquina / cierre y reapertura de sesión: **NO ejecutado** automáticamente (ver §13).
- Aislamiento entre cuentas de Windows distintas: **diferido a validación manual** (no hay segunda cuenta utilizable).

## 12. Solución de problemas

| Síntoma | Causa / acción |
|---|---|
| `BROKER_CA_MATERIAL_MISSING` | aprovisionar la CA (§4) antes de arrancar |
| `luis-sol-agent.exe pair` termina de inmediato | falta `SolAgent\broker_ca\ca.cert.pem`: `manage.ps1 set-ca` |
| La tarea no arranca el Agente (resultado `0x80070002`) | el exe/ruta no es visible para Task Scheduler. En entornos con virtualización de `%LOCALAPPDATA%` (Python de Microsoft Store, sandboxes) los archivos escritos allí no existen para el sistema real: instalar con `-Root` fuera de AppData o usar Python de python.org |
| Agente "no disponible" tras matarlo | esperado hasta el siguiente latido (`sol_agent_online_threshold_seconds`) |
| Segundo `run` sale con código 3 | ya hay una instancia (mutex); es el comportamiento correcto |
| `upgrade` falla moviendo binarios | handles transitorios tras detener: el script reintenta 20 veces y restaura la versión anterior si falla |

## 13. Validación manual de reinicio / inicio de sesión (pendiente antes del tag de release)

`REAL_REBOOT_ACCEPTANCE=NOT_RUN`. Procedimiento exacto (en la máquina del usuario, con una instalación de prueba o real):

1. Backend nativo detenido. Instalar con `manage.ps1 install ... -Start`; emparejar; `manage.ps1 status` → proceso `YES`.
2. **Reiniciar Windows** (no solo cerrar sesión). Iniciar sesión con el mismo usuario. Sin abrir nada manualmente, esperar ~1 min.
3. `manage.ps1 status`: `AGENT_PROCESS_RUNNING=YES`, `session=1` (sesión interactiva), `lastResult` sin error; `agent.log` con
   `broker_unavailable` (backend aún apagado).
4. Arrancar el backend (`start-backend.ps1 -Background`). En ≤ 1 min: `agent_connected` + `heartbeat_ok` en el log y el Agente
   `available: true` en la UI (`/clients/<id>/sol-access`).
5. Repetir con *cerrar sesión / iniciar sesión*. Lanzar manualmente `luis-sol-agent.exe run` con el autoarranque activo → código 3.
6. Con una segunda cuenta de Windows (si existe): verificar que no puede leer `SolAgent\` ni descifrar la identidad (DPAPI).
7. Registrar resultados y adjuntarlos a la evidencia del bloque.

## 14. Aceptación ejecutada en G (sin SUNAT)

Backend nativo real (PostgreSQL desechable en Docker, CA provisionada con la herramienta, broker `127.0.0.1:8995/8996`) +
Agente empaquetado real instalado con `-Root` (tarea de Task Scheduler, sesión 1): instancia única; mTLS y latidos; reconexión
tras reinicio real del backend; reinicio del backend con sesión `IN_PROGRESS` (contexto humano perdido: sin grant ni
entrega); arranque en frío con backend apagado; kill abrupto + reinicio por tarea (mutex libre, identidad intacta, sin
re-ejecución del job); `browser-check` con el Chromium instalado; upgrade alpha.1 → alpha.2 (identidad idéntica byte a byte);
desinstalación (identidad conservada) → reinstalación (misma identidad, sin re-emparejar) → purga explícita (solo identidad de
prueba); ACL de la identidad (solo el usuario actual). Pruebas automáticas: `test_g_packaging_and_provisioning.py`,
`test_g_repository_audit.py`, `test_g_operator_pair_cli.py`.
