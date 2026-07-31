<#
.SYNOPSIS
    Genera una copia de seguridad conjunta de la base de datos y del volumen
    de almacenamiento documental (Bloque 5, ADR-0055) en backups/<BackupId>/.

.DESCRIPTION
    El backend permanece detenido durante toda la captura (cero escrituras
    nuevas mientras se toma el dump y se archiva el volumen). Se recalcula el
    SHA-256 del 100% de los blobs registrados (ACTIVE/SUPERSEDED/DELETED) --
    nunca una muestra -- y el resultado se deja documentado en manifest.json
    junto al dump y al archivo del volumen.

    Limitacion conocida y aceptada: el dump de Postgres y el archivo del
    volumen no se capturan dentro de una transaccion distribuida real; detener
    el backend elimina el riesgo de escritura concurrente, pero no convierte
    la operacion en atomica a nivel de sistema de archivos subyacente.

.PARAMETER BackupId
    Identificador de esta copia (subcarpeta bajo backups/). Por defecto, una
    marca de tiempo. Usado internamente por restore.ps1 para su snapshot de
    seguridad previo (p. ej. "pre_restore_20260724_101500").

.PARAMETER Internal
    Modo interno: no reinicia el backend al finalizar (usado por restore.ps1,
    que gestiona su propio ciclo de arranque/parada tras la restauracion).
#>

param(
    [string]$BackupId,
    [switch]$Internal
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackupsDir = Join-Path $RepoRoot "backups"

. (Join-Path $PSScriptRoot "_backup_lib.ps1")

if (-not $BackupId) {
    $BackupId = Get-Date -Format "yyyyMMdd_HHmmss"
}

$BackupDir = Join-Path $BackupsDir $BackupId
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}

$pgUser = Get-EnvValue -RepoRoot $RepoRoot -Name "POSTGRES_USER"
$pgDb = Get-EnvValue -RepoRoot $RepoRoot -Name "POSTGRES_DB"

Push-Location $RepoRoot
try {
    Write-Host "Deteniendo backend (captura sin escrituras concurrentes)..." -ForegroundColor Cyan
    Invoke-NativeStep { docker compose stop backend } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo detener el backend antes del backup (codigo $LASTEXITCODE)"
    }

    try {
        $databaseFile = Join-Path $BackupDir "database.sql"
        Write-Host "Volcando base de datos..." -ForegroundColor Cyan
        $dumpLines = Invoke-NativeStep { docker compose exec -T db pg_dump -U $pgUser --clean --if-exists $pgDb }
        if ($LASTEXITCODE -ne 0) {
            throw "pg_dump fallo (codigo $LASTEXITCODE) -- backup abortado"
        }
        [System.IO.File]::WriteAllLines($databaseFile, $dumpLines, (New-Object System.Text.UTF8Encoding $false))
        $databaseSha256 = (Get-FileHash -Path $databaseFile -Algorithm SHA256).Hash.ToLower()

        Write-Host "Archivando volumen de documentos..." -ForegroundColor Cyan
        $archiveResult = Invoke-DocumentStorageCli -CliArgs @("archive", "--output", "/backups/$BackupId/volume.tar.gz")

        Write-Host "Verificando el 100% de los blobs registrados (no una muestra)..." -ForegroundColor Cyan
        $verifyResult = Invoke-DocumentStorageCli -CliArgs @("verify")

        Write-Host "Obteniendo revision de Alembic..." -ForegroundColor Cyan
        $alembicOutput = Invoke-NativeStep { docker compose run --rm -T --no-deps backend alembic current }
        if ($LASTEXITCODE -ne 0) {
            throw "alembic current fallo (codigo $LASTEXITCODE) -- backup abortado"
        }
        $alembicHead = ($alembicOutput | Select-Object -Last 1).ToString().Trim()

        # Version/commit de la aplicacion en el momento del backup (Seccion 14 del
        # plan). Se lee del propio repositorio en el host, nunca de dentro del
        # contenedor -- no depende de que la imagen tenga git instalado. Si no hay
        # commit disponible (repo sin historial, o git no instalado), se registra
        # explicitamente como "unknown" en vez de fallar todo el backup por esto.
        $gitCommit = $null
        try {
            $gitCommit = (git -C $RepoRoot rev-parse HEAD 2>$null)
        } catch {
            $gitCommit = $null
        }
        if (-not $gitCommit) { $gitCommit = "unknown" }

        $verificationOk = Test-IntegrityVerificationOk -VerifyResult $verifyResult

        # Fase 3 / Bloque 3.1 (ADR-0064): key_id + fingerprint de la clave de
        # cifrado activa de credenciales de integracion -- nunca la clave en
        # si. Si la integracion no esta configurada en este entorno, ambos
        # campos quedan null explicitamente (no es un error del backup).
        $integrationKeyInfo = Invoke-IntegrationKeyInfoCli
        $integrationTokenKeyId = $integrationKeyInfo.key_id
        $integrationTokenKeyFingerprint = $integrationKeyInfo.fingerprint

        $manifest = [ordered]@{
            backup_id               = $BackupId
            created_at_utc          = (Get-Date).ToUniversalTime().ToString("o")
            app_version             = $gitCommit
            alembic_head            = $alembicHead
            database_sha256         = $databaseSha256
            volume_sha256           = $archiveResult.volume_sha256
            document_file_row_count = $verifyResult.total_rows
            blob_count              = $archiveResult.blob_count
            integration_token_key_id          = $integrationTokenKeyId
            integration_token_key_fingerprint = $integrationTokenKeyFingerprint
            verification            = [ordered]@{
                ok            = $verificationOk
                verified_ok   = $verifyResult.verified_ok
                mismatches    = $verifyResult.mismatches
                missing_blobs = $verifyResult.missing_blobs
            }
        }
        $manifestFile = Join-Path $BackupDir "manifest.json"
        $manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestFile -Encoding utf8

        if (-not $verificationOk) {
            Write-Host "ADVERTENCIA: la verificacion de integridad encontro anomalias -- revisa $manifestFile" -ForegroundColor Yellow
        } else {
            Write-Host "Verificacion de integridad: OK ($($verifyResult.verified_ok) blobs)" -ForegroundColor Green
        }
        Write-Host "Copia de seguridad creada en: $BackupDir" -ForegroundColor Green
    } finally {
        if (-not $Internal) {
            Write-Host "Reiniciando backend..." -ForegroundColor Cyan
            Invoke-NativeStep { docker compose start backend } | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "ADVERTENCIA: no se pudo reiniciar el backend automaticamente (codigo $LASTEXITCODE) -- reinicialo manualmente con 'docker compose start backend'." -ForegroundColor Red
            }
        }
    }
} finally {
    Pop-Location
}
