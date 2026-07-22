<#
.SYNOPSIS
    Muestra logs de los servicios (todos por defecto, o uno especifico).

.PARAMETER Service
    Nombre del servicio (db, backend, frontend). Si se omite, muestra todos.
#>

param(
    [string]$Service = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    if ($Service) {
        docker compose logs -f $Service
    } else {
        docker compose logs -f
    }
} finally {
    Pop-Location
}
