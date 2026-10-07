# ADR-0073: Empaquetado del Agente SOL y despliegue local del broker (Fase 5, Bloque 5.2, Domain G)

## Estado
Aceptado

## Contexto
Los Domains E1-B a F validaron el stack supervisado (broker mTLS en el proceso del backend, Agente Windows nativo,
Chromium visible, solo autenticación hasta el aterrizaje). Faltaba hacerlo instalable, arrancable de forma
determinista y recuperable, sin ampliar el alcance de SUNAT.

## Decisión
1. **El Agente NO es un servicio de Windows.** Chromium visible y desafío humano exigen el escritorio interactivo; un
   servicio corre fuera de él (Session 0). Es una **aplicación por usuario** con autoarranque por **Task Scheduler**
   (al iniciar sesión, `Interactive`, `Limited`, sin instancias concurrentes).
2. **PyInstaller one-folder** (no one-file): sin autoextracción a `%TEMP%`, DLLs inspeccionables, upgrade/rollback por
   carpeta. Dos ejecutables sobre el mismo runtime: consola (pair/status) y sin consola (run). Dependencias de
   empaquetado fijadas y solo de desarrollo. Entorno de build: Windows x64, Python 3.13.14.
3. **Binarios e identidad separados** (`SolAgentApp` vs `SolAgent`): el upgrade y la desinstalación nunca borran la
   identidad; solo `purge` explícito (frase de confirmación). Chromium propiedad de la app; el estado autenticado sigue
   siendo efímero.
4. **Backend + broker nativos en Windows** (decisión del responsable). El broker es loopback-only y mTLS; un Agente
   nativo no puede alcanzar un broker en un contenedor Docker sin relajar esa regla (en E2 solo se hizo con una excepción
   de arnés). Se prefirió conservar la regla de Domain D antes que adaptar el despliegue Docker. El `docker-compose.yml` de
   desarrollo mantiene `SOL_BROKER_ENABLED=false`.
5. **CA aprovisionada explícitamente** (`app.broker.provision`), nunca autogenerada al arrancar; no sobrescribe; el Agente
   solo recibe el certificado público.
6. **Sin actualización automática**: upgrade manual con verificación de manifiesto SHA-256.
7. **Disponibilidad del Agente** = conexión registrada **y** latido reciente (`sol_agent_online_threshold_seconds`).

## Consecuencias
- El producto requiere un runtime Python nativo para el backend en la máquina del usuario.
- `ca.key.pem` sigue siendo PKCS#8 sin cifrar protegido por ACL (diseño de Domain D): riesgo residual documentado.
- Las builds son **sin firma** hasta disponer de un certificado de firma de código de confianza.
- El contexto JIT de F es local al proceso: un reinicio del backend exige una sesión supervisada nueva.
- La validación de reinicio real de Windows y de aislamiento entre cuentas queda como procedimiento manual documentado.
