<#
.SYNOPSIS
  Aprovisiona EXPLICITAMENTE la CA del broker SOL (paso intencional previo a SOL_BROKER_ENABLED=true).
.DESCRIPTION
  Nunca sobrescribe una CA existente. Restringe el directorio de la CA al usuario actual. Opcionalmente exporta el
  certificado PUBLICO (ca.cert.pem) que el Agente necesita. La clave privada de la CA no se imprime ni se exporta.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CaDir,
    [string]$ExportPublicTo = "",
    [string]$Python = "",
    [switch]$ExportOnly
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
if (-not $Python) { $Python = Join-Path $repo ".venv-win-backend\Scripts\python.exe" }
if ($CaDir.StartsWith($repo, [System.StringComparison]::OrdinalIgnoreCase)) { throw "La CA debe vivir FUERA del repositorio: $CaDir" }
Push-Location (Join-Path $repo "backend")
try {
    $args = @("-m", "app.broker.provision", "--ca-dir", $CaDir)
    if ($ExportPublicTo) { $args += @("--export-public", $ExportPublicTo) }
    if ($ExportOnly) { $args += "--export-only" }
    & $Python @args
    if ($LASTEXITCODE -ne 0) { throw "El aprovisionamiento fallo ($LASTEXITCODE)." }
} finally { Pop-Location }
