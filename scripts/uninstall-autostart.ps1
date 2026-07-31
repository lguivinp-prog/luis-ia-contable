<#
.SYNOPSIS
    Elimina la tarea de inicio automatico de Luis IA Contable, si existe
    (bloque transversal de operacion local, ver ADR-0066). Idempotente:
    si la tarea no existe, no hace nada y no reporta error. Nunca toca
    contenedores, volumenes ni datos -- solo la tarea del Programador de
    tareas de Windows.
#>

$ErrorActionPreference = "Stop"
$TaskName = "Luis IA Contable - Inicio local"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "La tarea '$TaskName' no estaba instalada -- nada que hacer." -ForegroundColor Cyan
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Tarea '$TaskName' eliminada. El inicio automatico quedo desactivado." -ForegroundColor Green
Write-Host "El acceso directo manual (create-desktop-shortcut.ps1) sigue funcionando con normalidad." -ForegroundColor Cyan
