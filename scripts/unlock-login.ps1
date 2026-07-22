<#
.SYNOPSIS
    Desbloqueo administrativo local de un identificador de login (ver
    docs/DECISIONS/ADR-0009-login-lockout.md). No borra historial.

.PARAMETER Email
    Correo/identificador a desbloquear.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Email
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    docker compose exec backend python -m app.cli.unlock_login $Email
} finally {
    Pop-Location
}
