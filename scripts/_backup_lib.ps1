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

function Invoke-NativeStep {
    <#
    .SYNOPSIS
        Ejecuta un bloque que invoca un comando nativo (docker, psql, etc.)
        sin que PowerShell 5.1 pueda convertir una linea de progreso escrita
        a stderr (p. ej. "Container ... Stopping") en un error terminante
        bajo $ErrorActionPreference = "Stop" -- ese es el bug reproducido en
        la ronda de cierre tecnico del Bloque 3.1.

        NUNCA decide por si sola si el comando fallo: solo evita la
        conversion implicita de stderr en error. El llamador sigue siendo
        responsable de revisar $LASTEXITCODE explicitamente justo despues de
        llamar a esta funcion -- varios comandos de este repositorio (p. ej.
        `document_storage_archive verify`) usan codigos de salida distintos
        de cero para resultados validos no criticos, asi que un umbral unico
        aqui ocultaria informacion real en vez de solo evitar el falso
        positivo del stderr.
    #>
    param([Parameter(Mandatory = $true)][scriptblock]$ScriptBlock)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 0
    try {
        return & $ScriptBlock
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Invoke-DocumentStorageCli {
    <#
    Ejecuta un subcomando del CLI declarativo de archivado (`archive`,
    `extract`, `verify`) dentro de un contenedor efimero de la imagen
    `backend` (nunca en el host) y devuelve el objeto JSON que imprime. Ver
    backend/app/cli/document_storage_archive.py.
    #>
    param([string[]]$CliArgs)
    $output = Invoke-NativeStep {
        docker compose run --rm -T --no-deps backend python -m app.cli.document_storage_archive @CliArgs
    }
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

function Invoke-IntegrationKeyInfoCli {
    <#
    Ejecuta app/cli/integration_key_info.py dentro de un contenedor efimero
    de la imagen `backend` y devuelve {key_id, fingerprint} (ambos null si
    la integracion Google no esta configurada en este entorno). Ver
    ADR-0064 (Fase 3 / Bloque 3.1).
    #>
    $output = Invoke-NativeStep {
        docker compose run --rm -T --no-deps backend python -m app.cli.integration_key_info
    }
    if ($LASTEXITCODE -ne 0) {
        throw "integration_key_info fallo con codigo $LASTEXITCODE"
    }
    $jsonLine = ($output | Where-Object { $_ -like "{*" } | Select-Object -Last 1)
    if (-not $jsonLine) {
        throw "integration_key_info no devolvio JSON: $output"
    }
    return $jsonLine | ConvertFrom-Json
}

function Invoke-GoogleIntegrationKeyReconciliation {
    <#
    .SYNOPSIS
        Tras restaurar la base de datos, conserva o purga las conexiones
        Google segun si la clave de cifrado activa coincide con la del
        manifiesto restaurado (key_id + fingerprint, ver ADR-0064 / Seccion
        14.3 del plan de Fase 3).

    .PARAMETER SourceManifest
        Objeto del manifest.json de la copia que se acaba de restaurar
        (no el snapshot de seguridad).
    #>
    param($SourceManifest, [string]$PgUser, [string]$PgDb)

    $tableExists = Invoke-NativeStep {
        docker compose exec -T db psql -U $PgUser -d $PgDb -tAc `
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'google_oauth_connections')"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo comprobar si google_oauth_connections existe (codigo $LASTEXITCODE)"
    }
    if (($tableExists | Select-Object -Last 1).ToString().Trim() -ne "t") {
        Write-Host "google_oauth_connections no existe en la copia restaurada -- omitiendo reconciliacion." -ForegroundColor Cyan
        return
    }

    $currentKeyInfo = Invoke-IntegrationKeyInfoCli
    $sourceKeyId = $SourceManifest.integration_token_key_id
    $sourceFingerprint = $SourceManifest.integration_token_key_fingerprint

    $keysMatch = ($null -ne $currentKeyInfo.key_id) -and ($null -ne $sourceKeyId) -and
                 ($currentKeyInfo.key_id -eq $sourceKeyId) -and
                 ($null -ne $sourceFingerprint) -and ($currentKeyInfo.fingerprint -eq $sourceFingerprint)

    if ($keysMatch) {
        Write-Host "Clave de cifrado de integraciones coincide (key_id + fingerprint) -- conexiones Google conservadas." -ForegroundColor Green
        return
    }

    Write-Host "Clave de cifrado de integraciones NO coincide (o el manifiesto no la incluye) -- purgando tokens y marcando NEEDS_REAUTH." -ForegroundColor Yellow
    $purgeSql = @"
UPDATE google_oauth_connections
SET status = 'NEEDS_REAUTH',
    access_token_encrypted = NULL,
    refresh_token_encrypted = NULL,
    access_token_expires_at = NULL,
    token_key_id = NULL,
    reauth_required_at = NOW(),
    reauth_reason = 'ENCRYPTION_KEY_CHANGED'
WHERE status = 'ACTIVE';
"@
    Invoke-NativeStep { $purgeSql | docker compose exec -T db psql -U $PgUser -d $PgDb } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo purgar los tokens Google tras el cambio de clave de cifrado (codigo $LASTEXITCODE)"
    }
    Write-Host "Conexiones Google activas marcadas NEEDS_REAUTH (ENCRYPTION_KEY_CHANGED)." -ForegroundColor Yellow
}
