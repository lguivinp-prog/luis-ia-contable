<#
.SYNOPSIS
    Harness reproducible del orquestador de arranque local (bloque
    transversal de operacion local, ver ADR-0066): ejercita
    start-luis-ia-contable.ps1/stop-luis-ia-contable.ps1 contra un doble
    falso de `docker` (scripts/tests/_fake_docker) para probar de forma
    rapida y deterministica escenarios de exito y de fallo -- sin depender
    de que Docker real este disponible o en un estado concreto -- y contra
    Docker real para las propiedades que solo tienen sentido con la pila
    real ya en marcha (paths con espacios, otro directorio de trabajo,
    ausencia de secretos en logs).

.DESCRIPTION
    Cada caso reporta PASS/FAIL de forma explicita; el script termina con
    exit code 0 solo si todos los casos pasaron.
#>

param(
    [switch]$SkipRealDockerCases
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$StartScript = Join-Path $RepoRoot "scripts\start-luis-ia-contable.ps1"
$StopScript = Join-Path $RepoRoot "scripts\stop-luis-ia-contable.ps1"
$FakeDockerDir = Join-Path $PSScriptRoot "_fake_docker"

. (Join-Path $RepoRoot "scripts\_backup_lib.ps1")

$failures = @()
$originalPath = $env:PATH

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        $script:failures += $Message
        Write-Host "  FALLO: $Message" -ForegroundColor Red
    } else {
        Write-Host "  OK: $Message" -ForegroundColor Green
    }
}

function Invoke-StartWithFakeDocker {
    param([string]$Mode, [string[]]$ExtraArgs = @())
    $env:PATH = "$FakeDockerDir;$originalPath"
    $env:FAKE_DOCKER_MODE = $Mode
    try {
        $output = Invoke-NativeStep {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -NoBrowser -NoInteractive @ExtraArgs 2>&1
        }
        return [pscustomobject]@{ Output = $output; ExitCode = $LASTEXITCODE }
    } finally {
        $env:PATH = $originalPath
        Remove-Item Env:\FAKE_DOCKER_MODE -ErrorAction SilentlyContinue
    }
}

Write-Host "=== Caso: Docker no disponible ===" -ForegroundColor Cyan
$env:PATH = ($originalPath -split ";" | Where-Object { $_ -notmatch "(?i)docker" }) -join ";"
try {
    $result = Invoke-NativeStep {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -NoBrowser -NoInteractive 2>&1
    }
    $exit = $LASTEXITCODE
} finally {
    $env:PATH = $originalPath
}
Assert-True ($exit -ne 0) "exit code distinto de cero cuando 'docker' no esta en PATH"
Assert-True (($result -join "`n") -match "Docker no esta instalado") "mensaje claro de Docker no disponible"

Write-Host "=== Caso: Docker Engine detenido (con timeout corto) ===" -ForegroundColor Cyan
$result = Invoke-StartWithFakeDocker -Mode "engine_down" -ExtraArgs @("-DockerEngineTimeoutSeconds", "3", "-PollIntervalSeconds", "1")
Assert-True ($result.ExitCode -ne 0) "exit code distinto de cero cuando el motor Docker no responde"
Assert-True (($result.Output -join "`n") -match "Docker Desktop no respondio") "mensaje claro de motor Docker detenido"

Write-Host "=== Caso: Docker Engine tarda en responder y luego inicia ===" -ForegroundColor Cyan
$dockerInfoCounterFile = Join-Path $env:TEMP "fake_docker_info_fail_counter.txt"
Remove-Item $dockerInfoCounterFile -ErrorAction SilentlyContinue
$env:PATH = "$FakeDockerDir;$originalPath"
$env:FAKE_DOCKER_MODE = "healthy_fast"
$env:FAKE_DOCKER_INFO_FAIL_COUNT = "2"
try {
    $slowResult = Invoke-NativeStep {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -NoBrowser -NoInteractive -DockerEngineTimeoutSeconds 30 -TimeoutSeconds 10 -PollIntervalSeconds 1 2>&1
    }
    $slowExit = $LASTEXITCODE
} finally {
    $env:PATH = $originalPath
    Remove-Item Env:\FAKE_DOCKER_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:\FAKE_DOCKER_INFO_FAIL_COUNT -ErrorAction SilentlyContinue
    Remove-Item $dockerInfoCounterFile -ErrorAction SilentlyContinue
}
Assert-True ($slowExit -eq 0) "el arranque tiene exito una vez que Docker Desktop responde, tras varios reintentos"
Assert-True ((($slowResult -join "`n") -match "Esperando Docker Desktop\.\.\.")) "se muestra 'Esperando Docker Desktop...' mientras el motor no responde"

Write-Host "=== Caso: docker compose up -d db con exit code distinto de cero ===" -ForegroundColor Cyan
$result = Invoke-StartWithFakeDocker -Mode "compose_up_fails"
Assert-True ($result.ExitCode -ne 0) "exit code distinto de cero cuando compose up -d db falla"
Assert-True (($result.Output -join "`n") -match "docker compose up -d db fallo") "mensaje claro de fallo en compose up -d db"

Write-Host "=== Caso: fallo de Alembic impide abrir el navegador ===" -ForegroundColor Cyan
$result = Invoke-StartWithFakeDocker -Mode "alembic_fails" -ExtraArgs @("-TimeoutSeconds", "10", "-PollIntervalSeconds", "1")
Assert-True ($result.ExitCode -ne 0) "exit code distinto de cero cuando alembic upgrade head falla"
Assert-True (($result.Output -join "`n") -match "alembic upgrade head fallo") "mensaje claro de fallo de Alembic"
Assert-True (-not (($result.Output -join "`n") -match "\[6/6\] Abriendo")) "el navegador NUNCA se abre si Alembic fallo"

Write-Host "=== Caso: flujo completo exitoso (docker falso, healthcheck inmediato) ===" -ForegroundColor Cyan
$result = Invoke-StartWithFakeDocker -Mode "healthy_fast" -ExtraArgs @("-TimeoutSeconds", "10", "-PollIntervalSeconds", "1")
Assert-True ($result.ExitCode -eq 0) "exit code 0 en flujo completo exitoso"
$joinedOutput = $result.Output -join "`n"
Assert-True ($joinedOutput -match "\[1/6\] Verificando Docker") "etapa 1/6 presente"
Assert-True ($joinedOutput -match "\[2/6\] Iniciando base de datos") "etapa 2/6 presente"
Assert-True ($joinedOutput -match "\[3/6\] Aplicando actualizaciones") "etapa 3/6 presente"
Assert-True ($joinedOutput -match "\[4/6\] Iniciando y verificando servicios") "etapa 4/6 presente"
Assert-True ($joinedOutput -match "\[5/6\] Sincronizando informacion local") "etapa 5/6 presente"
Assert-True ($joinedOutput -match "\[6/6\] Abriendo Luis IA Contable") "etapa 6/6 presente"
# Orden robusto: Alembic (3/6) debe completarse ANTES de exigir backend
# saludable (dentro de 4/6) -- nunca al reves.
$alembicIndex = [array]::IndexOf($result.Output, ($result.Output | Where-Object { $_ -match "Migraciones aplicadas" } | Select-Object -First 1))
$backendHealthyIndex = [array]::IndexOf($result.Output, ($result.Output | Where-Object { $_ -match "^Backend saludable\.$" } | Select-Object -First 1))
Assert-True ($alembicIndex -ge 0 -and $backendHealthyIndex -gt $alembicIndex) "Alembic corre y termina ANTES de exigir backend saludable"
# "El navegador solo abre al final": la etapa de apertura debe ser
# estrictamente la ULTIMA en el orden de salida, despues de sincronizar.
$syncIndex = [array]::IndexOf($result.Output, ($result.Output | Where-Object { $_ -match "Sincronizacion local completada" } | Select-Object -First 1))
$openIndex = [array]::IndexOf($result.Output, ($result.Output | Where-Object { $_ -match "\[6/6\] Abriendo" } | Select-Object -First 1))
Assert-True ($openIndex -gt $syncIndex -and $syncIndex -ge 0) "la etapa de apertura ocurre despues de sincronizar, nunca antes"

Write-Host "=== Caso: SKIPPED_LOCKED abre el navegador sin sincronizar de nuevo ===" -ForegroundColor Cyan
$result = Invoke-StartWithFakeDocker -Mode "sync_skipped_locked" -ExtraArgs @("-TimeoutSeconds", "10", "-PollIntervalSeconds", "1")
Assert-True ($result.ExitCode -eq 0) "exit code 0 cuando la sincronizacion ya esta en curso en otra instancia"
$skippedJoined = $result.Output -join "`n"
Assert-True ($skippedJoined -match "ya esta en curso") "mensaje claro de sincronizacion ya en curso"
Assert-True ($skippedJoined -match "\[6/6\] Abriendo Luis IA Contable") "el navegador se abre igual con SKIPPED_LOCKED"

Write-Host "=== Caso: PARTIAL_FAILURE abre el navegador con advertencia ===" -ForegroundColor Cyan
$result = Invoke-StartWithFakeDocker -Mode "sync_partial_failure" -ExtraArgs @("-TimeoutSeconds", "10", "-PollIntervalSeconds", "1")
Assert-True ($result.ExitCode -eq 2) "exit code 2 (documentado) cuando hay fallo parcial"
$partialJoined = $result.Output -join "`n"
Assert-True ($partialJoined -match "ADVERTENCIA.*fallaron durante la sincronizacion") "advertencia clara de fallo parcial"
Assert-True ($partialJoined -match "\[6/6\] Abriendo Luis IA Contable") "el navegador se abre igual con PARTIAL_FAILURE (el sistema sigue siendo utilizable)"
Assert-True ($partialJoined -match "bbbbbbbb") "nunca se oculta que tenant fallo (aparece en la salida)"
Assert-True (-not ($partialJoined -match "Traceback|at System\.|Exception:")) "nunca se muestra una traza tecnica al usuario normal"

Write-Host "=== Caso: FATAL_FAILURE nunca abre el navegador ===" -ForegroundColor Cyan
$result = Invoke-StartWithFakeDocker -Mode "sync_fatal_failure" -ExtraArgs @("-TimeoutSeconds", "10", "-PollIntervalSeconds", "1")
Assert-True ($result.ExitCode -ne 0) "exit code distinto de cero cuando la sincronizacion es FATAL_FAILURE"
$fatalJoined = $result.Output -join "`n"
Assert-True ($fatalJoined -match "irrecuperable") "mensaje claro de fallo irrecuperable"
Assert-True (-not ($fatalJoined -match "\[6/6\] Abriendo")) "el navegador NUNCA se abre ante FATAL_FAILURE"

Write-Host "=== Caso: healthcheck nunca saludable -> timeout finito ===" -ForegroundColor Cyan
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$result = Invoke-StartWithFakeDocker -Mode "never_healthy" -ExtraArgs @("-TimeoutSeconds", "3", "-PollIntervalSeconds", "1")
$sw.Stop()
Assert-True ($result.ExitCode -ne 0) "exit code distinto de cero cuando el servicio nunca queda saludable"
Assert-True (($result.Output -join "`n") -match "no alcanzo el estado 'healthy'") "mensaje claro de timeout de healthcheck"
Assert-True ($sw.Elapsed.TotalSeconds -lt 30) "el timeout es finito (no espera indefinida) -- tardo $([math]::Round($sw.Elapsed.TotalSeconds,1))s"

Write-Host "=== Caso: start ejecutado dos veces seguidas (docker falso) ===" -ForegroundColor Cyan
$first = Invoke-StartWithFakeDocker -Mode "healthy_fast" -ExtraArgs @("-TimeoutSeconds", "10", "-PollIntervalSeconds", "1")
$second = Invoke-StartWithFakeDocker -Mode "healthy_fast" -ExtraArgs @("-TimeoutSeconds", "10", "-PollIntervalSeconds", "1")
Assert-True ($first.ExitCode -eq 0 -and $second.ExitCode -eq 0) "dos ejecuciones consecutivas terminan ambas con exit code 0"

Write-Host "=== Caso: ruta del proyecto con espacios ===" -ForegroundColor Cyan
$spacedRoot = Join-Path $env:TEMP "Luis IA Contable Test"
if (Test-Path $spacedRoot) { Remove-Item -Recurse -Force $spacedRoot }
New-Item -ItemType Directory -Path (Join-Path $spacedRoot "scripts") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $spacedRoot "logs\startup") -Force | Out-Null
Copy-Item (Join-Path $RepoRoot "scripts\_backup_lib.ps1") (Join-Path $spacedRoot "scripts\_backup_lib.ps1")
Copy-Item $StartScript (Join-Path $spacedRoot "scripts\start-luis-ia-contable.ps1")
Set-Content -Path (Join-Path $spacedRoot ".env") -Value "FRONTEND_PORT=3010`nPOSTGRES_USER=x`nPOSTGRES_DB=x" -Encoding utf8
try {
    $env:PATH = "$FakeDockerDir;$originalPath"
    $env:FAKE_DOCKER_MODE = "healthy_fast"
    $spacedResult = Invoke-NativeStep {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $spacedRoot "scripts\start-luis-ia-contable.ps1") -NoBrowser -NoInteractive -TimeoutSeconds 10 -PollIntervalSeconds 1 2>&1
    }
    $spacedExit = $LASTEXITCODE
} finally {
    $env:PATH = $originalPath
    Remove-Item Env:\FAKE_DOCKER_MODE -ErrorAction SilentlyContinue
}
Assert-True ($spacedExit -eq 0) "el flujo completo funciona cuando la ruta del proyecto contiene espacios"
Assert-True ((Join-Path $spacedRoot "logs\startup" | Get-ChildItem -Filter "startup-*.log" -ErrorAction SilentlyContinue).Count -gt 0) "el log se escribe correctamente bajo una ruta con espacios"
Remove-Item -Recurse -Force $spacedRoot -ErrorAction SilentlyContinue

Write-Host "=== Caso: logs sin secretos ===" -ForegroundColor Cyan
$logsDir = Join-Path $RepoRoot "logs\startup"
$forbidden = @("POSTGRES_PASSWORD", "SESSION_SECRET", "client_secret", "ENCRYPTION_KEY", "dev-local-only-not-secret")
$logFiles = Get-ChildItem -Path $logsDir -Filter "startup-*.log" -ErrorAction SilentlyContinue
$leaked = $false
foreach ($file in $logFiles) {
    $content = Get-Content -Path $file.FullName -Raw
    foreach ($needle in $forbidden) {
        if ($content -match [regex]::Escape($needle)) {
            $leaked = $true
            Write-Host "  Se encontro '$needle' en $($file.Name)" -ForegroundColor Red
        }
    }
}
Assert-True (-not $leaked) "ningun log de arranque contiene secretos conocidos (password/token/client_secret/clave de cifrado)"

Write-Host "=== Caso: tarea programada -- delay, instancias multiples, sin credenciales ===" -ForegroundColor Cyan
$InstallAutostartScript = Join-Path $RepoRoot "scripts\install-autostart.ps1"
$UninstallAutostartScript = Join-Path $RepoRoot "scripts\uninstall-autostart.ps1"
$TaskName = "Luis IA Contable - Inicio local"
try {
    Invoke-NativeStep { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $InstallAutostartScript -LogonDelaySeconds 45 } | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) "install-autostart.ps1 termina con exit code 0"

    $taskXml = [string](Export-ScheduledTask -TaskName $TaskName)
    Assert-True ($taskXml -match "<Delay>PT45S</Delay>") "la tarea programada contiene el delay configurado (PT45S)"
    Assert-True ($taskXml -match "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>") "la tarea no permite instancias paralelas (IgnoreNew)"
    Assert-True ($taskXml -match [regex]::Escape($RepoRoot)) "la tarea usa la ruta absoluta real del proyecto"
    Assert-True ($taskXml -match "<LogonType>InteractiveToken</LogonType>") "la tarea usa LogonType interactivo (sin contraseña almacenada)"
    Assert-True (-not ($taskXml -match "Password|<Credential")) "la tarea no contiene ninguna credencial"

    # Idempotencia: reinstalar no debe fallar ni duplicar la tarea.
    Invoke-NativeStep { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $InstallAutostartScript } | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) "reinstalar la tarea es idempotente (no falla)"
    $taskCountAfterReinstall = (Get-ScheduledTask -TaskName $TaskName).Count
    Assert-True ((@(Get-ScheduledTask -TaskName $TaskName)).Count -eq 1) "reinstalar la tarea no la duplica"
} finally {
    Invoke-NativeStep { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UninstallAutostartScript } | Out-Null
}
$stillExists = $false
try {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
    $stillExists = $true
} catch {
    $stillExists = $false
}
Assert-True (-not $stillExists) "la tarea queda desinstalada tras las pruebas (idempotente, sin dejar rastro)"

if (-not $SkipRealDockerCases) {
    Write-Host "=== Caso: ejecucion desde otro directorio de trabajo (Docker real) ===" -ForegroundColor Cyan
    Push-Location $env:WINDIR
    try {
        $realResult = Invoke-NativeStep {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -NoBrowser -NoInteractive -TimeoutSeconds 60 2>&1
        }
        $realExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Assert-True ($realExit -eq 0) "el script funciona igual cuando se invoca desde un directorio de trabajo distinto al del proyecto"
    Assert-True ((($realResult -join "`n") -match [regex]::Escape("Raiz del proyecto: $RepoRoot"))) "la raiz del proyecto se resuelve por la ubicacion del script, no por el directorio actual"

    Write-Host "=== Caso: stop conserva volumenes (Docker real) ===" -ForegroundColor Cyan
    $volumesBefore = docker volume ls --format "{{.Name}}" | Where-Object { $_ -match "luis-ia-contable" }
    Invoke-NativeStep { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StopScript -NoInteractive } | Out-Null
    $stopExit = $LASTEXITCODE
    $volumesAfter = docker volume ls --format "{{.Name}}" | Where-Object { $_ -match "luis-ia-contable" }
    Assert-True ($stopExit -eq 0) "stop-luis-ia-contable.ps1 termina con exit code 0"
    Assert-True (@($volumesBefore).Count -eq @($volumesAfter).Count) "los volumenes con datos (postgres_data, document_storage) se conservan tras detener"
    # Reinicia para dejar el entorno como estaba antes del harness.
    Invoke-NativeStep { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -NoBrowser -NoInteractive -TimeoutSeconds 90 } | Out-Null

    Write-Host "=== Caso: base de datos atrasada en 0014 llega a 0015 durante el arranque (Docker real) ===" -ForegroundColor Cyan
    Invoke-NativeStep { docker compose exec -T backend alembic downgrade 0014 } | Out-Null
    $downgradeExit = $LASTEXITCODE
    Assert-True ($downgradeExit -eq 0) "downgrade a 0014 para preparar el escenario reproducible"

    $staleCurrentBefore = Invoke-NativeStep { docker compose exec -T backend alembic current }
    Assert-True ((($staleCurrentBefore -join "`n") -match "0014")) "la base de prueba queda realmente en 0014 antes del arranque"

    $staleResult = Invoke-NativeStep {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -NoBrowser -NoInteractive -TimeoutSeconds 90 2>&1
    }
    $staleExit = $LASTEXITCODE
    $staleJoined = $staleResult -join "`n"
    Assert-True ($staleExit -eq 0) "el arranque completo tiene exito partiendo de una base atrasada en 0014"

    # Orden robusto probado contra Docker real: Alembic debe completarse
    # ANTES de exigir backend saludable -- nunca el ciclo "backend no
    # healthy -> no se ejecuta Alembic -> timeout".
    $staleAlembicIndex = [array]::IndexOf($staleResult, ($staleResult | Where-Object { $_ -match "Migraciones aplicadas" } | Select-Object -First 1))
    $staleBackendHealthyIndex = [array]::IndexOf($staleResult, ($staleResult | Where-Object { $_ -match "^Backend saludable\.$" } | Select-Object -First 1))
    Assert-True ($staleAlembicIndex -ge 0) "Alembic se ejecuto durante el arranque con la base atrasada"
    Assert-True ($staleBackendHealthyIndex -gt $staleAlembicIndex) "Alembic termino ANTES de que se exigiera backend saludable"
    Assert-True ($staleJoined -match "(?m)^Backend saludable\.$") "el backend termina saludable"
    Assert-True ($staleJoined -match "Frontend disponible\.") "el frontend termina disponible"
    Assert-True ($staleJoined -match "\[5/6\] Sincronizando informacion local") "startup_sync se ejecuto"
    Assert-True ($staleJoined -match "\[6/6\] Abriendo Luis IA Contable") "el navegador se abre al final, incluso partiendo de una base atrasada"

    $staleCurrentAfter = Invoke-NativeStep { docker compose exec -T backend alembic current }
    Assert-True ((($staleCurrentAfter -join "`n") -match "0015")) "la base de datos termina en la revision 0015 tras el arranque"
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "FALLO: $($failures.Count) verificaciones incorrectas." -ForegroundColor Red
    exit 1
}
Write-Host "OK: todas las verificaciones del orquestador de arranque local pasaron." -ForegroundColor Green
exit 0
