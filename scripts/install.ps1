<#
.SYNOPSIS
    Primera instalacion de Luis IA Contable: verifica Docker, crea .env si falta,
    y construye las imagenes. Idempotente: puede ejecutarse varias veces sin dano.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "== Luis IA Contable: instalacion ==" -ForegroundColor Cyan

try {
    docker info *> $null
} catch {
    Write-Error "Docker Desktop no parece estar corriendo. Inicia Docker Desktop y vuelve a ejecutar este script."
    exit 1
}

$envPath = Join-Path $RepoRoot ".env"
$envExamplePath = Join-Path $RepoRoot ".env.example"

if (-not (Test-Path $envPath)) {
    Copy-Item $envExamplePath $envPath
    Write-Host "Se creo .env a partir de .env.example. Revisa y ajusta los valores antes de continuar (especialmente contrasenas y DEV_SEED_ADMIN_PASSWORD)." -ForegroundColor Yellow
} else {
    Write-Host ".env ya existe, no se sobrescribe." -ForegroundColor Green
}

Push-Location $RepoRoot
try {
    docker compose build
} finally {
    Pop-Location
}

Write-Host "Instalacion completada. Ejecuta .\scripts\start.ps1 para iniciar el sistema." -ForegroundColor Green
