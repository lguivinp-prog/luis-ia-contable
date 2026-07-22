<#
.SYNOPSIS
    Inicia el stack completo (db, backend, frontend) y espera a que esten healthy.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    docker compose up -d

    Write-Host "Esperando a que los servicios esten healthy..." -ForegroundColor Cyan
    $maxWaitSeconds = 120
    $elapsed = 0
    while ($elapsed -lt $maxWaitSeconds) {
        $statuses = docker compose ps --format json | ForEach-Object { $_ | ConvertFrom-Json }
        $unhealthy = $statuses | Where-Object { $_.Health -and $_.Health -ne "healthy" }
        if (-not $unhealthy) {
            break
        }
        Start-Sleep -Seconds 3
        $elapsed += 3
    }

    docker compose ps

    $backendPort = (Get-Content (Join-Path $RepoRoot ".env") | Select-String "^BACKEND_PORT=") -replace "BACKEND_PORT=", ""
    $frontendPort = (Get-Content (Join-Path $RepoRoot ".env") | Select-String "^FRONTEND_PORT=") -replace "FRONTEND_PORT=", ""
    if (-not $backendPort) { $backendPort = 8000 }
    if (-not $frontendPort) { $frontendPort = 3000 }

    Write-Host ""
    Write-Host "Luis IA Contable esta corriendo:" -ForegroundColor Green
    Write-Host "  Frontend: http://localhost:$frontendPort"
    Write-Host "  API / Swagger: http://localhost:$backendPort/docs"
} finally {
    Pop-Location
}
