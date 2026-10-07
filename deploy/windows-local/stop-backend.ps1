<#
.SYNOPSIS
  Detiene el backend nativo iniciado con start-backend.ps1 -Background (mata el ARBOL de procesos: en Windows el
  `python.exe` de un venv es un lanzador cuyo hijo es el proceso real de uvicorn).
#>
[CmdletBinding()]
param([string]$LogDir = "")
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
if (-not $LogDir) { $LogDir = Join-Path $repo ".g-runtime\backend-logs" }
$pidFile = Join-Path $LogDir "backend.pid"
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host "BACKEND_PID_FILE=ABSENT"; return }
$id = [int](Get-Content -LiteralPath $pidFile)
if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
    & taskkill.exe /PID $id /T /F | Out-Null
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Process -Id $id -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    Write-Host "BACKEND_STOPPED=$id (arbol completo)"
}
Remove-Item -LiteralPath $pidFile -Force
