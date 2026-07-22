<#
.SYNOPSIS
    Reinicia el entorno de desarrollo desde cero: borra volumenes (incluida la base
    de datos) y vuelve a levantar, migrar y sembrar datos ficticios.
    Accion DESTRUCTIVA. Pide confirmacion explicita. Solo para desarrollo local.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptDir = $PSScriptRoot

Write-Host "ADVERTENCIA: esto eliminara TODOS los datos locales (base de datos incluida)." -ForegroundColor Red
$confirmation = Read-Host "Escribe 'REINICIAR' (en mayusculas) para continuar"

if ($confirmation -ne "REINICIAR") {
    Write-Host "Operacion cancelada." -ForegroundColor Cyan
    exit 0
}

Push-Location $RepoRoot
try {
    docker compose down -v
} finally {
    Pop-Location
}

& (Join-Path $ScriptDir "start.ps1")
& (Join-Path $ScriptDir "migrate.ps1")
& (Join-Path $ScriptDir "seed.ps1")

Write-Host "Entorno de desarrollo reiniciado desde cero." -ForegroundColor Green
