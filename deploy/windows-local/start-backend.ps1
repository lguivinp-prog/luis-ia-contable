<#
.SYNOPSIS
  Arranca el backend NATIVO con el broker SOL habilitado (Domain G). Falla cerrado si falta la CA.
.DESCRIPTION
  El broker es loopback-only (127.0.0.1) con mTLS y vive en el MISMO proceso que el backend (un solo worker).
  Nunca autogenera la CA. Con -Background escribe un log por arranque en -LogDir con retencion acotada.
#>
[CmdletBinding()]
param(
    [string]$EnvFile = "",
    [string]$Python = "",
    [int]$Port = 8000,
    [switch]$Migrate,
    [switch]$Background,
    [string]$LogDir = "",
    [int]$KeepLogs = 10
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path (Join-Path $here "..\..")).Path
if (-not $EnvFile) { $EnvFile = Join-Path $here "sol-local.env" }
if (-not $Python) { $Python = Join-Path $repo ".venv-win-backend\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "No existe $EnvFile (copie sol-local.env.example)." }

foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith("#")) { continue }
    $k, $v = $t -split "=", 2
    [System.Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process")
}
if ($env:SOL_BROKER_ENABLED -ne "true") { throw "Este perfil exige SOL_BROKER_ENABLED=true." }
if (-not $env:SOL_BROKER_CA_DIR) { throw "SOL_BROKER_CA_DIR no definido." }
foreach ($f in "ca.cert.pem", "ca.key.pem") {
    if (-not (Test-Path -LiteralPath (Join-Path $env:SOL_BROKER_CA_DIR $f))) {
        throw "BROKER_CA_MATERIAL_MISSING: falta $f en $($env:SOL_BROKER_CA_DIR). Ejecute provision-broker-ca.ps1 (el arranque nunca autogenera la CA)."
    }
}
$env:PYTHONDONTWRITEBYTECODE = "1"
Push-Location (Join-Path $repo "backend")
try {
    if ($Migrate) {
        & $Python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head fallo." }
    }
    $uvicorn = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port", "--workers", "1")
    if (-not $Background) { & $Python @uvicorn; return }
    if (-not $LogDir) { $LogDir = Join-Path $repo ".g-runtime\backend-logs" }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $out = Join-Path $LogDir "backend-$stamp.out.log"
    $err = Join-Path $LogDir "backend-$stamp.err.log"
    $p = Start-Process -FilePath $Python -ArgumentList $uvicorn -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err
    Set-Content -LiteralPath (Join-Path $LogDir "backend.pid") -Value $p.Id
    Get-ChildItem -LiteralPath $LogDir -Filter "backend-*.log" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip ($KeepLogs * 2) | Remove-Item -Force
    Write-Host "BACKEND_PID=$($p.Id) LOG=$err"
} finally { Pop-Location }
