# ADR-0072: Almacenamiento de la identidad TLS del Agente Windows (PKCS#8 cifrado + frase de paso DPAPI)

## Estado
Aceptado

## Contexto
Fase 5, Bloque 5.2, Domain E2 introduce el primer runtime PRODUCTIVO del Agente Windows nativo
(`backend/windows_agent/`), endpoint del IPC agente↔broker ya implementado en Domain D/E1-B. Durante el
pairing (Domain D), el agente genera localmente un par clave privada/CSR y recibe un certificado firmado por
la CA local del broker — esa clave privada debe persistir entre reinicios del agente para reautenticar por
mTLS en cada arranque, pero nunca debe quedar expuesta en texto plano de forma durable en el disco del
usuario.

Spike B (`spike/phase5-sol-agent-ipc`, ver `SPIKE_REPORT.md`) demostró la viabilidad completa del IPC
agente↔broker pero **dejó deliberadamente sin resolver** la persistencia productiva de esta identidad
("Decisión abierta para 5.2 — persistencia de identidad del agente" — nunca implementó DPAPI, Windows
Credential Manager, TPM, ni almacén de certificados). Domain E2 cierra esa decisión.

## Decisión

### Mecanismo elegido: PKCS#8 cifrado + frase de paso DPAPI de usuario, nunca el Windows Certificate Store
Se evaluó la jerarquía de tres opciones exigida por el encargo de Domain E2:

**A. Almacén de certificados/llaves nativo de Windows (Windows Certificate Store / CNG).** Descartado para
v1. Dos razones: (1) expondría la clave privada al almacén COMPARTIDO del usuario (`CurrentUser\My`),
visible a cualquier proceso que lo enumere con los permisos adecuados — un radio de exposición mayor que un
archivo dedicado con ACL restrictiva; (2) razón técnica decisiva: el módulo estándar `ssl` de Python (única
biblioteca TLS ya usada en todo este repositorio desde Domain D — nunca se inventó un protocolo propio) **no
tiene ninguna API para presentar una clave residente en el almacén de certificados de Windows como identidad
de cliente TLS** — `SSLContext.load_cert_chain()` exige rutas de archivo, nunca un asa (handle) CNG. Usar el
almacén de certificados exigiría hablar SChannel directamente (una biblioteca TLS nueva y pesada, fuera de
proporción para este dominio).

**B. ELEGIDA: PKCS#8 cifrado en disco + frase de paso protegida con DPAPI, entregada a OpenSSL por callback
en memoria.** (`CryptProtectData`/`CryptUnprotectData`, alcance de **usuario**, nunca
`CRYPTPROTECT_LOCAL_MACHINE`; ACL de directorio restringida, mejor esfuerzo.)

1. Al emparejar, la clave privada (generada localmente, nunca transmitida) se cifra **en memoria** como PKCS#8
   cifrado (`BEGIN ENCRYPTED PRIVATE KEY`) con una frase de paso aleatoria de 256 bits (hex ASCII).
2. A disco van **solo**: `agent.key.enc.pem` (PKCS#8 cifrado) y `agent.keypass.dpapi` (la frase de paso
   protegida con DPAPI), más el certificado público y la metadata no secreta.
3. En cada arranque, la frase de paso se descifra con DPAPI en memoria y se pasa a
   `ssl.SSLContext.load_cert_chain(..., password=callable)`; OpenSSL descifra la clave en su propia memoria.

Resultado: `AGENT_PRIVATE_KEY_PLAINTEXT_EVER_WRITTEN_TO_FILESYSTEM=NO`. La garantía es **arquitectónica** (no
existe ninguna ruta de código que escriba la clave en claro), no un borrado posterior. Verificado
empíricamente que `load_cert_chain` acepta clave cifrada + callback `password` y falla cerrado ante frase
incorrecta.

**Limitación honesta:** la clave **no es no-exportable** — cualquier proceso que corra como el mismo usuario
de Windows puede invocar DPAPI y recuperarla (igual que cualquier secreto protegido por DPAPI de usuario). La
propiedad "no exportable" solo la daría la opción A, que exige otra biblioteca TLS (ver arriba).

**C. Rechazada explícitamente:** cualquier diseño que persistiera un PEM sin cifrar como formato durable.

### Diseño rechazado: "puente efímero" de disco (versión anterior de E2)
La primera implementación de E2 descifraba la clave con DPAPI, la escribía en claro a
`.key-bridge-<random>.tmp` dentro del directorio de datos, llamaba a `load_cert_chain(keyfile=...)` y borraba
el archivo en un `finally`. **Rechazado y eliminado del código productivo** porque viola el invariante
`AGENT_PRIVATE_KEY_PLAINTEXT_EVER_WRITTEN_TO_FILESYSTEM=NO`: ante una terminación abrupta (corte de energía,
`TerminateProcess`, BSOD) entre la escritura y el `unlink`, la clave queda en claro en disco, y aun en el caso
feliz el material pasó por el sistema de archivos (journal/MFT, copias de sombra, antivirus, indexadores). Ni
borrar más rápido, ni barrer residuos al arrancar, ni ACL, ni un nombre aleatorio, ni mover el archivo a
`%TEMP%`, ni EFS cierran esa brecha; son, como mucho, defensa en profundidad. El hallazgo de que
`load_cert_chain` exige *rutas* sigue siendo cierto — la solución es darle una ruta a un archivo que **ya está
cifrado** y proveer la frase de paso por callback.

### Alcance de DPAPI: identidad TLS únicamente, nunca la jerarquía de la bóveda SOL
Este mecanismo protege **exclusivamente** la identidad TLS del agente (clave privada de certificado mTLS).
Nunca sustituye, modifica, ni interactúa con la jerarquía criptográfica de la bóveda SOL (Bloque 5.1:
`vault_root_key`/`credential_dek`/contraseña maestra/secreto de recuperación) — ningún material de esa
jerarquía se persiste jamás con DPAPI ni por ningún otro mecanismo de `windows_agent/`. Esa bóveda sigue
viviendo exclusivamente en el backend/PostgreSQL, cifrada bajo su propia jerarquía ya congelada (ADR-0068 a
ADR-0071).

### Sin `pywin32`: bindings `ctypes` directos
DPAPI (`crypt32.dll`) y el mutex nombrado de instancia única (`kernel32.dll`, ver más abajo) se implementan
con bindings `ctypes` directos, sin añadir `pywin32` como dependencia nueva — ambas APIs son estables y
pequeñas, y el resto del repositorio ya prefiere la biblioteca estándar sobre dependencias nuevas cuando es
razonable. **Hallazgos reales de esta elección, confirmados ejecutando en Windows real** (no solo en el
contenedor Linux de pruebas, donde estas rutas de código son inalcanzables por diseño — ver la sección 7 del
encargo E2, "implementación explícita de Windows"):

- `ctypes.windll.kernel32.LocalFree(puntero)` sin `argtypes`/`restype` explícitos produce
  `ctypes.ArgumentError: int too long to convert` en Windows de 64 bits con punteros de dirección alta —
  invisible en pruebas superficiales con punteros pequeños. Corregido declarando prototipos exactos
  (`ctypes.POINTER(DATA_BLOB)`, `wintypes.DWORD`, etc.) para `CryptProtectData`/`CryptUnprotectData`/
  `LocalFree`.
- `ctypes.get_last_error()` inmediatamente después de una llamada vía `ctypes.windll.kernel32` (sin
  `use_last_error=True`) no refleja de forma fiable el `GetLastError()` real de esa llamada específica — un
  segundo proceso pudo adquirir el mismo mutex nombrado sin ser detectado (`ERROR_ALREADY_EXISTS` nunca leído
  correctamente). Corregido usando `ctypes.WinDLL("kernel32", use_last_error=True)` explícitamente y
  `ctypes.set_last_error(0)` antes de la llamada.

Ambos bugs solo se manifestaron al ejecutar la validación manual en Windows real (`windows_agent/
_manual_windows_validation.py`) — nunca habrían aparecido en un entorno de pruebas Linux/Docker, donde estas
rutas de código son estructuralmente inalcanzables. Esto confirma la necesidad de la validación manual en
Windows real exigida por el encargo, más allá de la suite automatizada.

### Instancia única del proceso productivo: mutex nombrado de Windows, no un PID file
`WindowsSingleInstanceGuard` usa `CreateMutexW` con un nombre determinista por instalación (nunca
`Global\` — evita exigir privilegios elevados de aislamiento de sesión). Windows libera el mutex
automáticamente si el proceso propietario termina, sin importar cómo terminó — a diferencia de un PID file,
que puede quedar obsoleto (proceso muerto sin limpiarlo, o PID reasignado). Deliberadamente **distinto** de
la elección de `startup_sync` (ADR-0066, advisory lock de PostgreSQL) — esa decisión rechazó explícitamente
un mutex de Windows porque "la sincronización real ocurre dentro del contenedor `backend`, no en el proceso
de PowerShell del host"; aquí la razón no aplica: el Agente corre nativamente en el host de Windows, donde un
mutex nombrado es el mecanismo correcto.

## Alternativas consideradas
- **TPM/Windows Hello for Business**: rechazado para v1 — exige hardware/configuración empresarial no
  garantizada en una instalación local de un solo usuario (mismo espíritu local-first de ADR-0066); posible
  mejora futura si el despliegue lo justifica.
- **Windows Credential Manager (`CredWrite`/`CredRead`)**: rechazado — tiene un límite de tamaño de blob
  pequeño (históricamente ~2.5KB) que podría no acomodar con holgura una clave RSA-2048 con margen para
  crecer, y ofrece la misma garantía de fondo que DPAPI de usuario (de hecho, lo usa internamente) sin
  ninguna ventaja clara sobre invocar DPAPI directamente con control total del formato de almacenamiento.
- **Cifrado con una contraseña maestra propia pedida al usuario**: rechazado — reintroduce el problema que
  DPAPI ya resuelve sin fricción (atar el secreto a la sesión de Windows), y el encargo prohíbe explícitamente
  cualquier prompt de credencial SOL en este flujo; una contraseña *distinta* de proceso solo añade fricción
  sin beneficio de seguridad claro frente a DPAPI de usuario.

## Consecuencias
- `AGENT_PRIVATE_KEY_PLAINTEXT_EVER_WRITTEN_TO_FILESYSTEM=NO`: la clave en claro solo existe en memoria del
  proceso (y, transitoriamente, en el `bytes` PEM de `pairing.py` antes de cifrarse). No hay archivo temporal,
  puente ni barrido de residuos que mantener.
- `AGENT_PRIVATE_KEY_EXPORTABLE=YES` para el mismo usuario de Windows (limitación aceptada de v1; ver B).
  Migrar a CNG/SChannel (clave no exportable) requiere reescribir el transporte TLS y queda como mejora futura.
- Validación en Windows real de este mecanismo pendiente de re-ejecutar con
  `windows_agent/_manual_windows_validation.py` (la ejecución previa validó el diseño anterior).
- Cualquier bloque futuro que necesite persistir un secreto NO-SOL en el Agente Windows (ej. un token de una
  integración futura) debe evaluar DPAPI de usuario como opción por defecto, citando este ADR, en vez de
  reinventar un mecanismo de cifrado propio.
- Si un despliegue futuro requiere una máquina compartida por múltiples usuarios de Windows con un mismo
  agente instalado, esta decisión debe revisarse — DPAPI de usuario ata la identidad a la sesión de inicio de
  sesión de un usuario de Windows específico, por diseño (ver sección "Alcance DPAPI" del documento de
  arquitectura del Agente, `docs/phase5/block5_2/BLOCK5_2_WINDOWS_AGENT.md`).
