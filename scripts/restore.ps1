<#
.SYNOPSIS
    Restaura la base de datos y el volumen de almacenamiento documental desde
    una copia generada por backup.ps1 (Bloque 5, ADR-0055).
    Accion DESTRUCTIVA: sobrescribe los datos actuales. Pide confirmacion
    explicita.

.PARAMETER BackupId
    Nombre de la subcarpeta bajo backups/ (p. ej. "20260724_101500") que
    contiene database.sql, volume.tar.gz y manifest.json.

.DESCRIPTION
    Antes de sobrescribir nada, toma un snapshot de seguridad del estado
    actual (BD + volumen + manifiesto) vía backup.ps1 -Internal, para poder
    deshacer si la restauracion falla a mitad de camino. Verifica el 100% de
    los blobs (no una muestra) tras restaurar; si la verificacion falla,
    revierte automaticamente al snapshot de seguridad y solo entonces decide
    si reiniciar el backend. Si ni siquiera esa reversion es segura, deja el
    backend detenido con un mensaje operativo claro.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupId
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackupsDir = Join-Path $RepoRoot "backups"
$BackupDir = Join-Path $BackupsDir $BackupId

. (Join-Path $PSScriptRoot "_backup_lib.ps1")

$databaseFile = Join-Path $BackupDir "database.sql"
$volumeFile = Join-Path $BackupDir "volume.tar.gz"
$manifestFile = Join-Path $BackupDir "manifest.json"

foreach ($required in @($databaseFile, $volumeFile, $manifestFile)) {
    if (-not (Test-Path $required)) {
        Write-Error "No se encontro el archivo requerido: $required"
        exit 1
    }
}

Write-Host "ADVERTENCIA: esto sobrescribira los datos actuales (base de datos y archivos documentales) con:" -ForegroundColor Yellow
Write-Host "  $BackupDir" -ForegroundColor Yellow
$confirmation = Read-Host "Escribe 'RESTAURAR' (en mayusculas) para continuar"
if ($confirmation -ne "RESTAURAR") {
    Write-Host "Operacion cancelada." -ForegroundColor Cyan
    exit 0
}

$pgUser = Get-EnvValue -RepoRoot $RepoRoot -Name "POSTGRES_USER"
$pgDb = Get-EnvValue -RepoRoot $RepoRoot -Name "POSTGRES_DB"

function Restore-From {
    param([string]$SourceDir, [string]$VolumeBackupId)
    Invoke-NativeStep {
        Get-Content (Join-Path $SourceDir "database.sql") | docker compose exec -T db psql -U $pgUser -d $pgDb
    } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "psql fallo restaurando database.sql (codigo $LASTEXITCODE)"
    }
    Invoke-DocumentStorageCli -CliArgs @("extract", "--input", "/backups/$VolumeBackupId/volume.tar.gz", "--confirm") | Out-Null
}

Push-Location $RepoRoot
try {
    Write-Host "Deteniendo backend..." -ForegroundColor Cyan
    Invoke-NativeStep { docker compose stop backend } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo detener el backend antes de restaurar (codigo $LASTEXITCODE)"
    }

    $safetyId = "pre_restore_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Write-Host "Capturando snapshot de seguridad del estado actual ($safetyId) antes de restaurar..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "backup.ps1") -BackupId $safetyId -Internal
    $safetyDir = Join-Path $BackupsDir $safetyId

    Write-Host "Restaurando base de datos desde $databaseFile..." -ForegroundColor Cyan
    Write-Host "Restaurando volumen de documentos desde $volumeFile..." -ForegroundColor Cyan
    $restoreFailed = $false
    try {
        Restore-From -SourceDir $BackupDir -VolumeBackupId $BackupId
    } catch {
        Write-Host "Fallo la restauracion: $($_.Exception.Message)" -ForegroundColor Red
        $restoreFailed = $true
    }

    $verificationOk = $false
    if (-not $restoreFailed) {
        Write-Host "Verificando el 100% de los blobs restaurados (no una muestra)..." -ForegroundColor Cyan
        $verifyResult = Invoke-DocumentStorageCli -CliArgs @("verify")
        $verificationOk = Test-IntegrityVerificationOk -VerifyResult $verifyResult
        if (-not $verificationOk) {
            Write-Host "La verificacion post-restauracion encontro anomalias." -ForegroundColor Red
            Write-Host ($verifyResult | ConvertTo-Json -Depth 6)
        }
    }

    if ($restoreFailed -or -not $verificationOk) {
        Write-Host "Revirtiendo automaticamente al snapshot de seguridad $safetyId..." -ForegroundColor Yellow
        $rollbackFailed = $false
        try {
            Restore-From -SourceDir $safetyDir -VolumeBackupId $safetyId
            $rollbackVerify = Invoke-DocumentStorageCli -CliArgs @("verify")
            $rollbackOk = Test-IntegrityVerificationOk -VerifyResult $rollbackVerify
            if (-not $rollbackOk) {
                $rollbackFailed = $true
            }
        } catch {
            Write-Host "Fallo tambien la reversion: $($_.Exception.Message)" -ForegroundColor Red
            $rollbackFailed = $true
        }

        if ($rollbackFailed) {
            Write-Host "ERROR CRITICO: la restauracion fallo y la reversion automatica tambien fallo." -ForegroundColor Red
            Write-Host "El backend permanece DETENIDO. Revisa manualmente la base de datos y el volumen antes de reiniciarlo." -ForegroundColor Red
            Write-Host "Snapshot de seguridad disponible en: $safetyDir" -ForegroundColor Red
            exit 1
        }

        Write-Host "Reversion exitosa: el estado anterior a la restauracion fue recuperado." -ForegroundColor Yellow
        Invoke-NativeStep { docker compose start backend } | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ADVERTENCIA: no se pudo reiniciar el backend automaticamente (codigo $LASTEXITCODE) -- reinicialo manualmente con 'docker compose start backend'." -ForegroundColor Red
        }
        Write-Host "Backend reiniciado sobre el estado ANTERIOR (la restauracion solicitada no se aplico)." -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Restauracion completada y verificada." -ForegroundColor Green
    $sourceManifest = Get-Content $manifestFile | ConvertFrom-Json
    Invoke-GoogleIntegrationKeyReconciliation -SourceManifest $sourceManifest -PgUser $pgUser -PgDb $pgDb

    Write-Host "Reiniciando backend..." -ForegroundColor Cyan
    Invoke-NativeStep { docker compose start backend } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ADVERTENCIA: no se pudo reiniciar el backend automaticamente (codigo $LASTEXITCODE) -- reinicialo manualmente con 'docker compose start backend'." -ForegroundColor Red
    }
    Write-Host "Restauracion completada." -ForegroundColor Green
} finally {
    Pop-Location
}
