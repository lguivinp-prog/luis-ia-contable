<#
.SYNOPSIS
    Corre linters y pruebas automatizadas de backend y frontend (actua como "CI local").
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    Write-Host "== Backend: ruff ==" -ForegroundColor Cyan
    docker compose exec backend ruff check .

    Write-Host "== Backend: pytest ==" -ForegroundColor Cyan
    docker compose exec backend pytest -q --cov=app --cov-report=term-missing

    Write-Host "== Frontend: tsc --noEmit ==" -ForegroundColor Cyan
    docker compose exec frontend npx tsc --noEmit

    Write-Host "== Frontend: eslint ==" -ForegroundColor Cyan
    docker compose exec frontend npx eslint .

    Write-Host "== Consistencia de referencias ADR ==" -ForegroundColor Cyan
    python scripts/check_adr_consistency.py

    Write-Host ""
    Write-Host "Todas las verificaciones pasaron." -ForegroundColor Green
} finally {
    Pop-Location
}
