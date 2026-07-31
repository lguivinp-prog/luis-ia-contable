<#
.SYNOPSIS
    Prueba automatizada de la ruta REAL de reconciliacion de clave de
    cifrado invocada por restore.ps1 (Bloque 3.1, ADR-0064): llama
    directamente a Invoke-GoogleIntegrationKeyReconciliation en
    _backup_lib.ps1 -- no una funcion criptografica aislada -- contra la
    base de datos real usada por restore.ps1.

.DESCRIPTION
    Caso obligatorio: un manifiesto con integration_token_key_id presente
    pero SIN integration_token_key_fingerprint nunca debe considerarse
    compatible, aunque el key_id coincida con el de la clave activa --
    debe purgar los tokens y marcar la conexion NEEDS_REAUTH/
    ENCRYPTION_KEY_CHANGED.

    Control: un manifiesto cuyo key_id Y fingerprint SI coinciden con la
    clave activa del entorno debe preservar la conexion ACTIVE intacta
    (para descartar que la prueba anterior pase solo porque la funcion
    purga siempre).

    Crea su propio tenant/usuario/conexion de prueba (prefijo
    TEST_BLOCK31_RECONCILIATION_HARNESS) en la base real y los elimina al
    finalizar, exitosamente o no. No imprime ningun valor de clave o
    fingerprint -- solo resultados booleanos.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

. (Join-Path $RepoRoot "scripts\_backup_lib.ps1")

$pgUser = Get-EnvValue -RepoRoot $RepoRoot -Name "POSTGRES_USER"
$pgDb = Get-EnvValue -RepoRoot $RepoRoot -Name "POSTGRES_DB"

function Invoke-Psql {
    <#
    -q (quiet) es imprescindible ademas de -tA: sin ella, psql tambien
    imprime la etiqueta de finalizacion de comando (p. ej. "INSERT 0 1")
    como una linea de salida separada incluso con -t, lo que corrompe la
    captura de un solo valor de un INSERT ... RETURNING.
    #>
    param([string]$Sql)
    $result = $Sql | docker compose exec -T db psql -U $pgUser -d $pgDb -v ON_ERROR_STOP=1 -tAq
    if ($LASTEXITCODE -ne 0) {
        throw "psql fallo (codigo $LASTEXITCODE) ejecutando la sentencia de la prueba de reconciliacion"
    }
    return $result
}

function Set-HarnessConnectionActive {
    param([string]$TenantId, [string]$UserId)
    Invoke-Psql @"
DELETE FROM google_oauth_connections WHERE tenant_id = '$TenantId';
INSERT INTO google_oauth_connections
    (tenant_id, user_id, google_account_email, google_account_id, scopes_granted,
     access_token_encrypted, refresh_token_encrypted, token_key_id, access_token_expires_at,
     status, first_connected_at, authorized_at)
VALUES
    ('$TenantId', '$UserId', 'harness@example.invalid', 'harness-sub-1', '["openid","email"]'::jsonb,
     decode('00', 'hex'), decode('00', 'hex'), 'harness-key', NOW() + interval '1 hour',
     'ACTIVE', NOW(), NOW());
"@ | Out-Null
}

function Get-HarnessConnectionRow {
    param([string]$TenantId)
    $row = Invoke-Psql @"
SELECT status, COALESCE(reauth_reason, ''),
       (access_token_encrypted IS NULL)::text,
       (refresh_token_encrypted IS NULL)::text,
       (access_token_expires_at IS NULL)::text,
       (token_key_id IS NULL)::text
FROM google_oauth_connections WHERE tenant_id = '$TenantId';
"@
    $fields = $row.Trim() -split '\|'
    return [pscustomobject]@{
        Status           = $fields[0].Trim()
        ReauthReason     = $fields[1].Trim()
        AccessTokenNull  = $fields[2].Trim()
        RefreshTokenNull = $fields[3].Trim()
        ExpiresAtNull    = $fields[4].Trim()
        TokenKeyIdNull   = $fields[5].Trim()
    }
}

$failures = @()
$tenantId = $null

try {
    Write-Host "Creando tenant/usuario/conexion de prueba (harness aislado)..." -ForegroundColor Cyan
    $tenantId = (Invoke-Psql "INSERT INTO tenants (name) VALUES ('TEST_BLOCK31_RECONCILIATION_HARNESS') RETURNING id;").Trim()
    $userId = (Invoke-Psql @"
INSERT INTO users (tenant_id, email, full_name, password_hash, role)
VALUES ('$tenantId', 'test-block31-harness@example.invalid', 'Test Harness', 'x', 'admin')
RETURNING id;
"@).Trim()

    $currentKeyInfo = Invoke-IntegrationKeyInfoCli
    if (-not $currentKeyInfo.key_id -or -not $currentKeyInfo.fingerprint) {
        throw "El entorno actual no tiene la clave de cifrado de integraciones configurada -- no se puede ejecutar este harness (ver INTEGRATION_TOKEN_ENCRYPTION_KEY/INTEGRATION_TOKEN_KEY_ID)."
    }

    # --- Caso obligatorio (Seccion 4): manifiesto con key_id pero SIN fingerprint ---
    Write-Host "Caso: manifiesto con key_id presente pero SIN integration_token_key_fingerprint..." -ForegroundColor Cyan
    Set-HarnessConnectionActive -TenantId $tenantId -UserId $userId
    $manifestMissingFingerprint = [pscustomobject]@{ integration_token_key_id = $currentKeyInfo.key_id }
    Invoke-GoogleIntegrationKeyReconciliation -SourceManifest $manifestMissingFingerprint -PgUser $pgUser -PgDb $pgDb

    $after1 = Get-HarnessConnectionRow -TenantId $tenantId
    if ($after1.Status -ne "NEEDS_REAUTH") { $failures += "Caso sin fingerprint: status esperado NEEDS_REAUTH, fue '$($after1.Status)'" }
    if ($after1.ReauthReason -ne "ENCRYPTION_KEY_CHANGED") { $failures += "Caso sin fingerprint: reauth_reason esperado ENCRYPTION_KEY_CHANGED, fue '$($after1.ReauthReason)'" }
    if ($after1.AccessTokenNull -ne "true") { $failures += "Caso sin fingerprint: access_token_encrypted deberia quedar NULL" }
    if ($after1.RefreshTokenNull -ne "true") { $failures += "Caso sin fingerprint: refresh_token_encrypted deberia quedar NULL" }
    if ($after1.ExpiresAtNull -ne "true") { $failures += "Caso sin fingerprint: access_token_expires_at deberia quedar NULL" }
    if ($after1.TokenKeyIdNull -ne "true") { $failures += "Caso sin fingerprint: token_key_id deberia quedar NULL" }

    # --- Control: manifiesto con key_id + fingerprint realmente coincidentes ---
    Write-Host "Control: manifiesto con key_id + fingerprint coincidentes (no debe purgar)..." -ForegroundColor Cyan
    Set-HarnessConnectionActive -TenantId $tenantId -UserId $userId
    $manifestMatching = [pscustomobject]@{
        integration_token_key_id          = $currentKeyInfo.key_id
        integration_token_key_fingerprint = $currentKeyInfo.fingerprint
    }
    Invoke-GoogleIntegrationKeyReconciliation -SourceManifest $manifestMatching -PgUser $pgUser -PgDb $pgDb

    $after2 = Get-HarnessConnectionRow -TenantId $tenantId
    if ($after2.Status -ne "ACTIVE") { $failures += "Control: status esperado ACTIVE (conexion debia preservarse), fue '$($after2.Status)'" }
    if ($after2.AccessTokenNull -ne "false") { $failures += "Control: access_token_encrypted no deberia purgarse cuando key_id+fingerprint coinciden" }
    if ($after2.TokenKeyIdNull -ne "false") { $failures += "Control: token_key_id no deberia purgarse cuando key_id+fingerprint coinciden" }
}
finally {
    if ($tenantId) {
        Write-Host "Limpiando datos de prueba del harness..." -ForegroundColor Cyan
        Invoke-Psql @"
DELETE FROM google_oauth_connections WHERE tenant_id = '$tenantId';
DELETE FROM users WHERE tenant_id = '$tenantId';
DELETE FROM tenants WHERE id = '$tenantId';
"@ | Out-Null
    }
}

if ($failures.Count -gt 0) {
    Write-Host "FALLO: $($failures.Count) aserciones incorrectas:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

Write-Host "OK: Invoke-GoogleIntegrationKeyReconciliation (ruta real de restore.ps1) purga correctamente cuando falta el fingerprint del manifiesto, y preserva la conexion cuando key_id+fingerprint coinciden." -ForegroundColor Green
exit 0
