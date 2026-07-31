<#
.SYNOPSIS
    Prueba reproducible del manejo de stderr/exit code corregido en
    Invoke-NativeStep (scripts/_backup_lib.ps1), exigida por la Seccion 5 de
    la ronda de cierre tecnico del Bloque 3.1.

.DESCRIPTION
    Nota de reproduccion honesta: se intento reproducir directamente, en
    esta misma sesion/host de PowerShell 5.1, el error original reportado
    (una linea de progreso en stderr con exit code 0, promovida a error
    terminante bajo $ErrorActionPreference = "Stop") tanto con
    `docker compose stop backend` como con un comando nativo sintetico
    (`cmd /c "echo ... 1>&2 & exit 0"`) -- en NINGUNO de los dos casos se
    reprodujo la promocion en este host concreto. Este es un problema real y
    documentado de PowerShell 5.1 cuyo disparo depende del host/consola que
    procesa el stream de error del comando nativo (varia segun si la consola
    esta adjunta, redirigida, o es un pipeline no interactivo) -- no
    reproducirse en este host no lo invalida como riesgo real en otros
    contextos de invocacion (doble clic, Programador de tareas de Windows,
    una consola PowerShell interactiva distinta). Por eso la correccion se
    aplica de forma general y defensiva de todas formas, y esta prueba
    demuestra las DOS propiedades exigidas explicitamente, de forma
    deterministica (sin depender de que el bug se manifieste primero):

    (a) Una linea de progreso en stderr con exit code 0 NUNCA debe abortar
        el script bajo Invoke-NativeStep, incluso con
        $ErrorActionPreference = "Stop" activo.
    (b) Un exit code distinto de cero SI debe poder detectarse y abortar el
        script (vía la revision explicita de $LASTEXITCODE que exige el
        patron, hecha por el llamador justo despues de invocar
        Invoke-NativeStep) -- la correccion nunca debe ocultar un fallo
        real.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $RepoRoot "scripts\_backup_lib.ps1")

$failures = @()

# --- Caso (a): stderr + exit code 0 no debe abortar ---
Write-Host "Caso (a): comando nativo que escribe a stderr pero termina con exit code 0..." -ForegroundColor Cyan
$ErrorActionPreference = "Stop"
$caseAThrew = $false
$caseAOutput = $null
try {
    $caseAOutput = Invoke-NativeStep { cmd /c "echo progress-line-on-stderr 1>&2 & exit 0" }
} catch {
    $caseAThrew = $true
}
if ($caseAThrew) {
    $failures += "Caso (a): Invoke-NativeStep aborto pese a que el comando termino con exit code 0 (solo escribio a stderr)"
}
if ($LASTEXITCODE -ne 0) {
    $failures += "Caso (a): se esperaba `$LASTEXITCODE = 0 tras el comando, fue $LASTEXITCODE"
}

# --- Caso (b): exit code distinto de cero SI debe detectarse y poder abortar ---
Write-Host "Caso (b): comando nativo que termina con exit code 1..." -ForegroundColor Cyan
$ErrorActionPreference = "Stop"
$caseBDetectedFailure = $false
try {
    Invoke-NativeStep { cmd /c "echo failure-line-on-stderr 1>&2 & exit 1" } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # Este es exactamente el patron que backup.ps1/restore.ps1 usan tras
        # cada llamada a Invoke-NativeStep -- el llamador revisa el codigo y
        # lanza el en un error real.
        throw "fallo simulado (codigo $LASTEXITCODE)"
    }
} catch {
    $caseBDetectedFailure = $true
}
if (-not $caseBDetectedFailure) {
    $failures += "Caso (b): un exit code distinto de cero no fue detectado -- la correccion oculto un fallo real"
}

if ($failures.Count -gt 0) {
    Write-Host "FALLO: $($failures.Count) aserciones incorrectas:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

Write-Host "OK: Invoke-NativeStep nunca aborta por una linea de progreso en stderr con exit code 0, y un exit code distinto de cero se sigue detectando y puede abortar el script." -ForegroundColor Green
exit 0
