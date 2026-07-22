<#
.SYNOPSIS
    Aplica las migraciones de Alembic dentro del contenedor backend.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    docker compose exec backend alembic upgrade head
    Write-Host "Migraciones aplicadas." -ForegroundColor Green
} finally {
    Pop-Location
}
