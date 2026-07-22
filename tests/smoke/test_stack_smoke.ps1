<#
.SYNOPSIS
    Smoke test del stack completo: verifica con peticiones HTTP reales que
    backend y frontend respondan tras "docker compose up". No sustituye a las
    pruebas automatizadas de backend/tests/; se corre despues de start.ps1.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$envLines = Get-Content (Join-Path $RepoRoot ".env")
$backendPort = ($envLines | Select-String "^BACKEND_PORT=") -replace "BACKEND_PORT=", ""
$frontendPort = ($envLines | Select-String "^FRONTEND_PORT=") -replace "FRONTEND_PORT=", ""
if (-not $backendPort) { $backendPort = 8000 }
if (-not $frontendPort) { $frontendPort = 3000 }

$failures = 0

function Test-Endpoint {
    param([string]$Name, [string]$Url, [int]$ExpectedStatus = 200)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -MaximumRedirection 0 -ErrorAction Stop
        $status = $response.StatusCode
    } catch [Microsoft.PowerShell.Commands.HttpResponseException] {
        $status = [int]$_.Exception.Response.StatusCode
    } catch {
        Write-Host "[FALLO] $Name ($Url): $($_.Exception.Message)" -ForegroundColor Red
        $script:failures++
        return
    }

    if ($status -eq $ExpectedStatus) {
        Write-Host "[OK] $Name -> $status" -ForegroundColor Green
    } else {
        Write-Host "[FALLO] $Name -> esperado $ExpectedStatus, recibido $status" -ForegroundColor Red
        $script:failures++
    }
}

Test-Endpoint -Name "Backend health/live" -Url "http://localhost:$backendPort/api/v1/health/live" -ExpectedStatus 200
Test-Endpoint -Name "Backend health/ready" -Url "http://localhost:$backendPort/api/v1/health/ready" -ExpectedStatus 200
Test-Endpoint -Name "Backend Swagger UI" -Url "http://localhost:$backendPort/docs" -ExpectedStatus 200
Test-Endpoint -Name "Frontend login (publico)" -Url "http://localhost:$frontendPort/login" -ExpectedStatus 200
# /clients esta protegido por proxy.ts y debe redirigir (307) si no hay sesion.
Test-Endpoint -Name "Frontend /clients sin sesion (protegido)" -Url "http://localhost:$frontendPort/clients" -ExpectedStatus 307

if ($failures -gt 0) {
    Write-Host ""
    Write-Host "$failures verificacion(es) fallaron." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Smoke test del stack completo: OK" -ForegroundColor Green
