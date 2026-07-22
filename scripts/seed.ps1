<#
.SYNOPSIS
    Carga datos ficticios de desarrollo (tenant, admin, catalogo, clientes ficticios).
    Idempotente: puede ejecutarse varias veces sin duplicar registros.
    NUNCA usar en un entorno con datos reales.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $RepoRoot
try {
    docker compose exec backend python -m app.seeds.seed_dev_data
} finally {
    Pop-Location
}
