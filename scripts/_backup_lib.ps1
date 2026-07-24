<#
.SYNOPSIS
    Funciones compartidas por backup.ps1 y restore.ps1 (Bloque 5, Seccion 14
    del plan). No se ejecuta directamente.
#>

function Get-EnvValue {
    param([string]$RepoRoot, [string]$Name)
    $envLines = Get-Content (Join-Path $RepoRoot ".env")
    return ($envLines | Select-String "^$Name=") -replace "$Name=", ""
}

function Invoke-DocumentStorageCli {
    <#
    Ejecuta un subcomando del CLI declarativo de archivado (`archive`,
    `extract`, `verify`) dentro de un contenedor efimero de la imagen
    `backend` (nunca en el host) y devuelve el objeto JSON que imprime. Ver
    backend/app/cli/document_storage_archive.py.
    #>
    param([string[]]$CliArgs)
    $output = docker compose run --rm -T --no-deps backend `
        python -m app.cli.document_storage_archive @CliArgs
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
        throw "document_storage_archive $($CliArgs -join ' ') fallo con codigo $LASTEXITCODE"
    }
    # El CLI imprime un unico objeto JSON por linea; se toma la ultima linea
    # que empiece con '{' por si Docker antepone alguna advertencia.
    $jsonLine = ($output | Where-Object { $_ -like "{*" } | Select-Object -Last 1)
    if (-not $jsonLine) {
        throw "document_storage_archive $($CliArgs -join ' ') no devolvio JSON: $output"
    }
    return $jsonLine | ConvertFrom-Json
}

function Test-IntegrityVerificationOk {
    param($VerifyResult)
    return ($VerifyResult.mismatches.Count -eq 0) -and ($VerifyResult.missing_blobs.Count -eq 0)
}
