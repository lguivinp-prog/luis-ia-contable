<#
.SYNOPSIS
    Registra una tarea del Programador de tareas de Windows que inicia Luis
    IA Contable automaticamente al iniciar sesion del usuario actual
    (bloque transversal de operacion local, ver ADR-0066). Opcional: el
    acceso directo manual funciona igual sin instalar esto.

.DESCRIPTION
    La tarea se ejecuta unicamente cuando el usuario actual inicia sesion
    (LogonType Interactive) -- no requiere almacenar ninguna contraseña, y
    no pide privilegios elevados (RunLevel Limited) salvo que la propia
    configuracion de Windows del equipo lo exija. Es idempotente: volver a
    ejecutar este script reemplaza la tarea existente en vez de duplicarla
    (`Register-ScheduledTask -Force`).

    Retraso de inicio (`LogonDelaySeconds`, 30s por defecto): Docker Desktop
    normalmente no esta listo en el instante exacto del inicio de sesion --
    el propio start-luis-ia-contable.ps1 ya tolera esto con su espera de
    Docker Engine (`-DockerEngineTimeoutSeconds`), pero dar un margen breve
    antes de lanzar el script reduce sondeos innecesarios en el caso comun.

    Politica de instancias multiples (`MultipleInstances = IgnoreNew`): si
    el disparador de esta tarea llegara a activarse dos veces (p. ej. un
    reinicio de sesion rapido), la segunda no lanza una segunda instancia
    mientras la primera siga corriendo -- capa adicional sobre el advisory
    lock de PostgreSQL que ya usa `startup_sync` (ver ADR-0066).
#>

param(
    [int]$StartupTimeoutSeconds = 180,
    [int]$LogonDelaySeconds = 30
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $RepoRoot "scripts\start-luis-ia-contable.ps1"
$TaskName = "Luis IA Contable - Inicio local"

if (-not (Test-Path $StartScript)) {
    throw "No se encontro $StartScript -- verifica que el repositorio este completo."
}

# El autostart corre sin consola interactiva -- nunca debe quedar esperando
# un Read-Host, y nunca abre el navegador antes de que el usuario haya
# terminado de iniciar sesion normalmente (se abre igual, en segundo plano,
# como cualquier programa de inicio).
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -NoInteractive -TimeoutSeconds $StartupTimeoutSeconds" `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# Delay real aplicado al disparador (formato de duracion ISO 8601) -- da
# margen a Docker Desktop antes de que el script empiece siquiera a
# sondearlo.
$trigger.Delay = "PT${LogonDelaySeconds}S"

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Inicia Luis IA Contable (Docker Compose + sincronizacion local) al iniciar sesion, con $LogonDelaySeconds s de margen para Docker Desktop." `
    -Force | Out-Null

Write-Host "Tarea '$TaskName' registrada: se ejecutara al iniciar sesion de $env:USERNAME (con ${LogonDelaySeconds}s de margen)." -ForegroundColor Green
Write-Host "Ruta del script invocado: $StartScript" -ForegroundColor Cyan
Write-Host "Politica de instancias multiples: IgnoreNew (no se lanza una segunda si ya hay una corriendo)." -ForegroundColor Cyan
Write-Host "Para desactivarlo, ejecuta scripts\uninstall-autostart.ps1." -ForegroundColor Cyan
