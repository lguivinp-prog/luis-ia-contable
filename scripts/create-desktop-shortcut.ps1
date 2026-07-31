<#
.SYNOPSIS
    Crea (o recrea) un acceso directo visible en el escritorio para iniciar
    Luis IA Contable con un solo doble clic (bloque transversal de
    operacion local, ver ADR-0066).

.DESCRIPTION
    Invoca start-luis-ia-contable.ps1 con ExecutionPolicy Bypass limitado a
    ese unico proceso (nunca cambia la politica de ejecucion del sistema),
    con el directorio de trabajo correcto, y reutiliza el icono ya existente
    del proyecto (favicon.ico del frontend) en vez de añadir un binario
    nuevo. Idempotente: volver a ejecutarlo sobrescribe el mismo archivo
    .lnk, nunca crea uno duplicado.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $RepoRoot "scripts\start-luis-ia-contable.ps1"

if (-not (Test-Path $StartScript)) {
    throw "No se encontro $StartScript -- verifica que el repositorio este completo."
}

$iconPath = Join-Path $RepoRoot "frontend\src\app\favicon.ico"
if (-not (Test-Path $iconPath)) {
    # No se añade ningun binario nuevo -- si el icono del proyecto no esta
    # disponible por algun motivo, se usa el propio icono de powershell.exe.
    $iconPath = "$PSHOME\powershell.exe,0"
} else {
    $iconPath = "$iconPath,0"
}

$desktopDir = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopDir "Luis IA Contable.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""
$shortcut.WorkingDirectory = $RepoRoot
$shortcut.IconLocation = $iconPath
$shortcut.Description = "Iniciar Luis IA Contable (Docker + sincronizacion local)"
$shortcut.WindowStyle = 1  # ventana normal, visible -- nunca oculta
$shortcut.Save()

Write-Host "Acceso directo creado: $shortcutPath" -ForegroundColor Green
Write-Host "Doble clic para iniciar Luis IA Contable." -ForegroundColor Cyan
