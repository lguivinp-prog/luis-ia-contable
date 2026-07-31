<#
.SYNOPSIS
    Detiene Luis IA Contable localmente (bloque transversal de operacion
    local, ver ADR-0066). Nunca elimina volumenes ni datos: usa
    `docker compose stop`, jamas `docker compose down -v`. La base de datos
    y los documentos almacenados quedan intactos para el proximo inicio.
#>

param(
    [switch]$NoInteractive
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "_backup_lib.ps1")

Push-Location $RepoRoot
try {
    Write-Host "Deteniendo Luis IA Contable (los datos y documentos se conservan)..." -ForegroundColor Cyan
    Invoke-NativeStep { docker compose stop } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose stop fallo (codigo $LASTEXITCODE)." -ForegroundColor Red
        if (-not $NoInteractive) {
            Read-Host "Presiona Enter para cerrar esta ventana"
        }
        exit 1
    }

    Write-Host ""
    Write-Host "Estado final de los servicios:" -ForegroundColor Cyan
    docker compose ps

    Write-Host ""
    Write-Host "Luis IA Contable detenido. Los volumenes (base de datos, documentos) se conservaron intactos." -ForegroundColor Green
} finally {
    Pop-Location
}

exit 0
