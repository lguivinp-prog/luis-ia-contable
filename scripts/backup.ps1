<#
.SYNOPSIS
    Genera una copia de seguridad de la base de datos en backups/.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackupsDir = Join-Path $RepoRoot "backups"

if (-not (Test-Path $BackupsDir)) {
    New-Item -ItemType Directory -Path $BackupsDir | Out-Null
}

$envLines = Get-Content (Join-Path $RepoRoot ".env")
$pgUser = ($envLines | Select-String "^POSTGRES_USER=") -replace "POSTGRES_USER=", ""
$pgDb = ($envLines | Select-String "^POSTGRES_DB=") -replace "POSTGRES_DB=", ""

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = Join-Path $BackupsDir "backup_$timestamp.sql"

Push-Location $RepoRoot
try {
    # --clean --if-exists incluye DROP antes de cada CREATE, para que restore.ps1
    # pueda aplicarse sobre una base de datos que ya tiene el esquema creado.
    $dumpLines = docker compose exec -T db pg_dump -U $pgUser --clean --if-exists $pgDb
    # Se escribe sin BOM (UTF8Encoding($false)) para que psql pueda restaurar el
    # archivo sin problemas; Out-File/Set-Content en Windows PowerShell 5.1 anaden BOM.
    [System.IO.File]::WriteAllLines($backupFile, $dumpLines, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Copia de seguridad creada: $backupFile" -ForegroundColor Green
} finally {
    Pop-Location
}
