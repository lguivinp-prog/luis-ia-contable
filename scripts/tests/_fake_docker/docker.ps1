<#
.SYNOPSIS
    Doble falso de `docker` para el harness de scripts/tests/Test-
    StartupOrchestrator.ps1 -- nunca toca Docker real. Su comportamiento se
    selecciona con la variable de entorno FAKE_DOCKER_MODE, para poder
    probar de forma deterministica y rapida escenarios de exito y de fallo
    de start-luis-ia-contable.ps1 sin depender de una pila Docker real.

    Refleja exactamente las invocaciones reales del orden corregido:
    `up -d db` -> `run --rm --no-deps backend alembic upgrade head` ->
    `up -d backend frontend` -> `exec -T backend python -m
    app.cli.startup_sync`.
#>

$mode = $env:FAKE_DOCKER_MODE
$joined = $args -join " "

function Exit-With { param([int]$Code) exit $Code }

if ($joined -eq "info") {
    if ($mode -eq "engine_down") {
        [Console]::Error.WriteLine("Cannot connect to the Docker daemon. Is the docker daemon running?")
        Exit-With 1
    }
    # Simula Docker Desktop todavia arrancando: falla las primeras N
    # llamadas a "docker info" (contador persistido en un archivo, ya que
    # cada invocacion es un proceso nuevo), luego responde con normalidad.
    if ($env:FAKE_DOCKER_INFO_FAIL_COUNT) {
        $counterFile = Join-Path $env:TEMP "fake_docker_info_fail_counter.txt"
        $remaining = if (Test-Path $counterFile) {
            [int](Get-Content $counterFile -Raw)
        } else {
            [int]$env:FAKE_DOCKER_INFO_FAIL_COUNT
        }
        if ($remaining -gt 0) {
            Set-Content -Path $counterFile -Value ($remaining - 1) -Encoding ascii
            [Console]::Error.WriteLine("Cannot connect to the Docker daemon. Is the docker daemon running?")
            Exit-With 1
        }
        Remove-Item $counterFile -ErrorAction SilentlyContinue
    }
    Write-Output "Server Version: fake"
    Exit-With 0
}

if ($joined -eq "compose up -d db") {
    if ($mode -eq "compose_up_fails") {
        [Console]::Error.WriteLine("Error response from daemon: fake failure")
        Exit-With 1
    }
    Write-Output " Container fake-db-1 Starting "
    Write-Output " Container fake-db-1 Started "
    Exit-With 0
}

if ($joined -eq "compose up -d backend frontend") {
    Write-Output " Container fake-backend-1 Starting "
    Write-Output " Container fake-frontend-1 Starting "
    Exit-With 0
}

if ($joined -like "compose ps * --format json") {
    if ($mode -eq "never_healthy") {
        Write-Output '{"Service":"fake","Health":"starting"}'
        Exit-With 0
    }
    Write-Output '{"Service":"fake","Health":"healthy"}'
    Exit-With 0
}

if ($joined -eq "compose run --rm --no-deps backend alembic upgrade head") {
    if ($mode -eq "alembic_fails") {
        [Console]::Error.WriteLine("FAILED: fake alembic failure")
        Exit-With 1
    }
    Write-Output "INFO  [alembic.runtime.migration] fake upgrade head"
    Exit-With 0
}

if ($joined -eq "compose exec -T backend python -m app.cli.startup_sync") {
    if ($mode -eq "sync_partial_failure") {
        Write-Output "[startup_sync] 2 tenant(s) descubierto(s)."
        Write-Output "[startup_sync] tenant aaaaaaaa: OK created=1 source_updated=0 source_completed=0 source_cancelled=0 already_exists=0"
        Write-Output "[startup_sync] tenant bbbbbbbb: ERROR (fallo simulado) -- se continua con los demas tenants"
        Write-Output "STARTUP_SYNC_STATUS=PARTIAL_FAILURE"
        Write-Output 'STARTUP_SYNC_RESULT_JSON={"result": "PARTIAL_FAILURE", "exit_code": 2, "tenants_total": 2, "tenants_failed": 1, "failed_tenants": [{"tenant_id": "bbbbbbbb-0000-0000-0000-000000000000", "error": "fallo simulado"}]}'
        Exit-With 2
    }
    if ($mode -eq "sync_skipped_locked") {
        Write-Output "[startup_sync] La sincronizacion inicial ya esta en curso en otra instancia -- se omite esta ejecucion (no es un error)."
        Write-Output "STARTUP_SYNC_STATUS=SKIPPED_LOCKED"
        Exit-With 0
    }
    if ($mode -eq "sync_fatal_failure") {
        [Console]::Error.WriteLine("[startup_sync] FATAL: no se pudo conectar a PostgreSQL (simulado)")
        Write-Output "STARTUP_SYNC_STATUS=FATAL_FAILURE"
        Write-Output 'STARTUP_SYNC_RESULT_JSON={"result": "FATAL_FAILURE", "exit_code": 1, "fatal_error": "no se pudo conectar a PostgreSQL (simulado)"}'
        Exit-With 1
    }
    Write-Output "[startup_sync] 1 tenant(s) descubierto(s)."
    Write-Output "[startup_sync] tenant aaaaaaaa: OK created=0 source_updated=0 source_completed=0 source_cancelled=0 already_exists=0"
    Write-Output "[startup_sync] Completado: 1 tenant(s), 0 fallo(s), 0.01s -- resultado: SUCCESS"
    Write-Output "STARTUP_SYNC_STATUS=SUCCESS"
    Write-Output 'STARTUP_SYNC_RESULT_JSON={"result": "SUCCESS", "exit_code": 0, "tenants_total": 1, "tenants_failed": 0}'
    Exit-With 0
}

if ($joined -eq "compose stop") {
    Write-Output " Container fake-db-1 Stopping "
    Write-Output " Container fake-db-1 Stopped "
    Exit-With 0
}

if ($joined -eq "compose ps") {
    Write-Output "NAME      IMAGE     COMMAND   SERVICE   CREATED   STATUS    PORTS"
    Exit-With 0
}

# Cualquier otra subcomando no contemplado explicitamente: exito silencioso,
# para no bloquear pasos que este harness no necesita ejercitar.
Exit-With 0
