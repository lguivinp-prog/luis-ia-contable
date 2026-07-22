<#
.SYNOPSIS
    Restaura la base de datos desde un archivo de copia de seguridad.
    Accion DESTRUCTIVA: sobrescribe los datos actuales. Pide confirmacion explicita.

.PARAMETER BackupFile
    Ruta al archivo .sql generado por backup.ps1.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $BackupFile)) {
    Write-Error "No se encontro el archivo de backup: $BackupFile"
    exit 1
}

Write-Host "ADVERTENCIA: esto sobrescribira los datos actuales de la base de datos con el contenido de:" -ForegroundColor Yellow
Write-Host "  $BackupFile" -ForegroundColor Yellow
$confirmation = Read-Host "Escribe 'RESTAURAR' (en mayusculas) para continuar"

if ($confirmation -ne "RESTAURAR") {
    Write-Host "Operacion cancelada." -ForegroundColor Cyan
    exit 0
}

$envLines = Get-Content (Join-Path $RepoRoot ".env")
$pgUser = ($envLines | Select-String "^POSTGRES_USER=") -replace "POSTGRES_USER=", ""
$pgDb = ($envLines | Select-String "^POSTGRES_DB=") -replace "POSTGRES_DB=", ""

Push-Location $RepoRoot
try {
    Get-Content $BackupFile | docker compose exec -T db psql -U $pgUser -d $pgDb
    Write-Host "Restauracion completada." -ForegroundColor Green
} finally {
    Pop-Location
}
