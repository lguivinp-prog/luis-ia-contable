<#
.SYNOPSIS
    Detiene el stack sin borrar datos (no usa -v).
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    docker compose down
    Write-Host "Sistema detenido. Los datos de la base de datos se conservan." -ForegroundColor Green
} finally {
    Pop-Location
}
