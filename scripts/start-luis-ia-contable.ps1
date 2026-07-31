<#
.SYNOPSIS
    Orquestador de inicio local de Luis IA Contable (bloque transversal de
    operacion local, ver ADR-0066): un unico punto de entrada, invocado por
    doble clic en el acceso directo o por la tarea de inicio automatico de
    Windows, que levanta PostgreSQL, aplica migraciones, levanta y verifica
    backend/frontend, sincroniza la informacion local (via el CLI
    `startup_sync`, nunca desde el ciclo de vida de FastAPI) y abre la
    interfaz en el navegador.

.DESCRIPTION
    Orden robusto (corregido tras la ronda de revision del checkpoint): las
    migraciones de Alembic se aplican inmediatamente despues de que
    PostgreSQL esta saludable, SIN esperar a que el backend alcance su
    propio healthcheck -- el backend, en desarrollo, puede tardar en quedar
    "healthy" por razones ajenas a la base de datos (compilacion, montaje de
    volumen, etc.), y esperarlo antes de migrar podria producir un ciclo de
    "backend no healthy -> no se aplica Alembic -> timeout" sin necesidad.
    `alembic upgrade head` se ejecuta en un contenedor efimero
    (`docker compose run --rm --no-deps backend ...`), no en el servicio
    `backend` de larga duracion.

.PARAMETER TimeoutSeconds
    Plazo maximo total (segundos) para cada etapa de espera de salud de un
    servicio (PostgreSQL/backend/frontend). Nunca es una espera infinita.

.PARAMETER DockerEngineTimeoutSeconds
    Plazo maximo (segundos) para esperar a que el motor Docker (Docker
    Desktop) responda -- tolera que Docker Desktop todavia este arrancando
    justo despues de iniciar sesion en Windows (ver ADR-0066, Seccion 6).

.PARAMETER PollIntervalSeconds
    Intervalo entre sondeos de salud durante cualquier espera (Docker
    Engine, PostgreSQL, backend, frontend).

.PARAMETER NoBrowser
    No abre el navegador al finalizar (util para pruebas automatizadas).

.PARAMETER NoInteractive
    No pide "Presiona Enter" al finalizar con error o advertencia (util para
    pruebas automatizadas y para invocaciones desde otro script). El acceso
    directo de escritorio NUNCA pasa este parametro, para que la ventana
    quede abierta si algo falla al iniciar por doble clic.

.PARAMETER ShowFullError
    Muestra la traza completa de cualquier error, en vez del mensaje breve
    normal (modo debug). No se llama "-Debug" para no chocar con el
    parametro comun de PowerShell del mismo nombre.
#>

param(
    [int]$TimeoutSeconds = 120,
    [int]$DockerEngineTimeoutSeconds = 120,
    [int]$PollIntervalSeconds = 2,
    [switch]$NoBrowser,
    [switch]$NoInteractive,
    [switch]$ShowFullError
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "_backup_lib.ps1")

$LogsDir = Join-Path $RepoRoot "logs\startup"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$LogFile = Join-Path $LogsDir "startup-$(Get-Date -Format 'yyyy-MM-dd').log"

# Retencion simple: conserva como maximo los ultimos 14 archivos de log de
# arranque -- nunca crece sin limite (ver ADR-0066, Seccion B.7).
$MAX_LOG_FILES = 14

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [$Level] $Message"
    Write-Host $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Invoke-LogRotation {
    $existing = Get-ChildItem -Path $LogsDir -Filter "startup-*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if ($existing.Count -gt $MAX_LOG_FILES) {
        $existing | Select-Object -Skip $MAX_LOG_FILES | Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Write-Stage {
    param([int]$Number, [string]$Title)
    Write-Log ""
    Write-Log "[$Number/6] $Title" -Level "STAGE"
}

function Show-FriendlyError {
    param([string]$Message, $ErrorRecord)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    if ($ShowFullError -and $ErrorRecord) {
        Write-Host ($ErrorRecord | Out-String) -ForegroundColor DarkRed
    }
    Write-Host "Detalles completos en: $LogFile" -ForegroundColor Yellow
    if ($ErrorRecord) {
        Add-Content -Path $LogFile -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [ERROR] $Message :: $($ErrorRecord.Exception.Message)" -Encoding utf8
    } else {
        Add-Content -Path $LogFile -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [ERROR] $Message" -Encoding utf8
    }
    if (-not $NoInteractive) {
        Read-Host "Presiona Enter para cerrar esta ventana"
    }
}

function Wait-DockerEngineReady {
    param([int]$TimeoutSeconds, [int]$PollIntervalSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $shownWaitingMessage = $false
    while ((Get-Date) -lt $deadline) {
        Invoke-NativeStep { docker info } | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        if (-not $shownWaitingMessage) {
            Write-Log "Esperando Docker Desktop..."
            $shownWaitingMessage = $true
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }
    return $false
}

function Wait-ComposeServiceHealthy {
    param([string]$ServiceName, [int]$TimeoutSeconds, [int]$PollIntervalSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $psOutput = Invoke-NativeStep { docker compose ps $ServiceName --format json }
        if ($psOutput) {
            $line = ($psOutput | Where-Object { $_ -like "{*" } | Select-Object -First 1)
            if ($line) {
                try {
                    $parsed = $line | ConvertFrom-Json
                    if ($parsed.Health -eq "healthy") {
                        return $true
                    }
                } catch {
                    # Salida todavia no parseable (contenedor recien creandose) -- se reintenta.
                }
            }
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }
    return $false
}

$partialFailureWarning = $false

try {
    Invoke-LogRotation
    Write-Log "== Luis IA Contable -- inicio local ==" -Level "STAGE"
    Write-Log "Raiz del proyecto: $RepoRoot"

    Push-Location $RepoRoot
    try {
        # --- [1/6] Verificando Docker ---
        Write-Stage 1 "Verificando Docker"
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker no esta instalado o no esta en el PATH. Instala Docker Desktop y vuelve a intentarlo."
        }
        if (-not (Wait-DockerEngineReady -TimeoutSeconds $DockerEngineTimeoutSeconds -PollIntervalSeconds $PollIntervalSeconds)) {
            throw "Docker Desktop no respondio dentro de $DockerEngineTimeoutSeconds segundos. Abrelo manualmente y espera a que este listo antes de reintentar."
        }
        Write-Log "Docker disponible y operativo."

        # --- [2/6] Iniciando base de datos ---
        Write-Stage 2 "Iniciando base de datos"
        Invoke-NativeStep { docker compose up -d db } | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose up -d db fallo (codigo $LASTEXITCODE). Revisa 'docker compose logs db' para mas detalle."
        }
        Write-Log "Esperando PostgreSQL saludable (hasta ${TimeoutSeconds}s)..."
        if (-not (Wait-ComposeServiceHealthy -ServiceName "db" -TimeoutSeconds $TimeoutSeconds -PollIntervalSeconds $PollIntervalSeconds)) {
            throw "PostgreSQL no alcanzo el estado 'healthy' dentro de $TimeoutSeconds segundos."
        }
        Write-Log "PostgreSQL saludable."

        # --- [3/6] Aplicando actualizaciones ---
        # Deliberado: Alembic corre aqui, contra un contenedor EFIMERO
        # (--rm --no-deps), inmediatamente despues de que PostgreSQL esta
        # saludable -- nunca espera a que el servicio backend de larga
        # duracion alcance su propio healthcheck (ver docstring del script).
        Write-Stage 3 "Aplicando actualizaciones"
        Invoke-NativeStep { docker compose run --rm --no-deps backend alembic upgrade head } | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "alembic upgrade head fallo (codigo $LASTEXITCODE). La base de datos puede haber quedado en un estado intermedio -- revisa el log antes de reintentar."
        }
        Write-Log "Migraciones aplicadas (alembic upgrade head)."

        # --- [4/6] Iniciando y verificando servicios ---
        Write-Stage 4 "Iniciando y verificando servicios"
        Invoke-NativeStep { docker compose up -d backend frontend } | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose up -d backend frontend fallo (codigo $LASTEXITCODE). Revisa 'docker compose logs' para mas detalle."
        }
        Write-Log "Esperando backend saludable (hasta ${TimeoutSeconds}s)..."
        if (-not (Wait-ComposeServiceHealthy -ServiceName "backend" -TimeoutSeconds $TimeoutSeconds -PollIntervalSeconds $PollIntervalSeconds)) {
            throw "El backend no alcanzo el estado 'healthy' dentro de $TimeoutSeconds segundos."
        }
        Write-Log "Backend saludable."
        Write-Log "Esperando frontend disponible (hasta ${TimeoutSeconds}s)..."
        if (-not (Wait-ComposeServiceHealthy -ServiceName "frontend" -TimeoutSeconds $TimeoutSeconds -PollIntervalSeconds $PollIntervalSeconds)) {
            throw "El frontend no alcanzo el estado 'healthy' dentro de $TimeoutSeconds segundos."
        }
        Write-Log "Frontend disponible."

        # --- [5/6] Sincronizando informacion local ---
        Write-Stage 5 "Sincronizando informacion local"
        $syncOutput = Invoke-NativeStep { docker compose exec -T backend python -m app.cli.startup_sync }
        $syncExitCode = $LASTEXITCODE
        foreach ($line in $syncOutput) { Write-Log $line }

        $statusLine = $syncOutput | Where-Object { $_ -match "^STARTUP_SYNC_STATUS=" } | Select-Object -Last 1
        $syncStatus = if ($statusLine) { ($statusLine -split "=", 2)[1].Trim() } else { "UNKNOWN" }

        switch ($syncStatus) {
            "SUCCESS" {
                Write-Log "Sincronizacion local completada sin fallos."
            }
            "SKIPPED_LOCKED" {
                Write-Log "La sincronizacion inicial ya esta en curso en otra instancia -- se continua sin sincronizar de nuevo."
            }
            "PARTIAL_FAILURE" {
                $partialFailureWarning = $true
                Write-Log "ADVERTENCIA: uno o mas tenants fallaron durante la sincronizacion local. El sistema sigue siendo utilizable -- revisa el detalle de cada tenant arriba y en: $LogFile" -Level "WARN"
            }
            "FATAL_FAILURE" {
                throw "La sincronizacion de arranque fallo de forma irrecuperable (codigo de salida $syncExitCode). Revisa el detalle arriba y en $LogFile antes de reintentar."
            }
            default {
                if ($syncExitCode -ne 0) {
                    throw "La sincronizacion de arranque devolvio un resultado no reconocido (codigo $syncExitCode)."
                }
            }
        }

        # --- [6/6] Abriendo Luis IA Contable ---
        Write-Stage 6 "Abriendo Luis IA Contable"
        $frontendPort = (Get-EnvValue -RepoRoot $RepoRoot -Name "FRONTEND_PORT").Trim()
        if (-not $frontendPort) { $frontendPort = "3000" }
        $frontendUrl = "http://localhost:$frontendPort"
        Write-Log "Interfaz disponible en: $frontendUrl"
        if (-not $NoBrowser) {
            Start-Process $frontendUrl | Out-Null
        }

        if ($partialFailureWarning) {
            Write-Log ""
            Write-Log "Luis IA Contable esta abierto, pero con una sincronizacion PARCIAL (ver advertencia arriba)." -Level "WARN"
            if (-not $NoInteractive) {
                Read-Host "Presiona Enter para cerrar esta ventana"
            }
            exit 2
        }

        Write-Log ""
        Write-Log "Luis IA Contable esta listo. Los contenedores siguen corriendo en segundo plano." -Level "STAGE"
    } finally {
        Pop-Location
    }
} catch {
    Show-FriendlyError -Message $_.Exception.Message -ErrorRecord $_
    exit 1
}

exit 0
