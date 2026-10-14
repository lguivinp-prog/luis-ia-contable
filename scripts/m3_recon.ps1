# M3 -- reconocimiento GUIADO por checkpoints (menu expandido antes del clic + llegada despues). UN solo comando:
#     .\scripts\m3_recon.ps1                 inicia Chromium (usted inicia sesion) y la sesion guiada
#     .\scripts\m3_recon.ps1 -Resume         RECUPERA el Chromium que esta herramienta dejo abierto con Q (sin volver a autenticarse)
#     .\scripts\m3_recon.ps1 -Attach 51234   igual, indicando el puerto que se mostro al salir
#     .\scripts\m3_recon.ps1 -Public       solo la tarjeta PUBLICA «Mis Declaraciones y Pagos»: NO requiere iniciar sesion (destino 4)
#     .\scripts\m3_recon.ps1 -Diagnose       ensayo de arranque SIN SUNAT (ejecutable, CDP, conexion, cierre) con la causa concreta de cualquier fallo
# La herramienta SOLO OBSERVA: usted hace todos los clics. No guarda credenciales. Salida fuera del repositorio. El depurador de Chromium escucha
# unicamente en 127.0.0.1 (puerto elegido por Chromium) y usa un perfil temporal propio; con Q el navegador se CONSERVA y con X se cierra.
param([string]$Out = "$env:USERPROFILE\m3-recon-v2", [int]$Attach = 0, [switch]$Resume, [switch]$Diagnose, [switch]$Public, [string]$EntryUrl = "")
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)   # sin BOM al canalizar comandos
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv-win-e2\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "No se encontro $py (venv con Playwright)."; exit 2 }
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repo ".pw-browsers-win-e2"
$extra = @()
foreach ($d in @("$env:USERPROFILE\m3-recon-principal", "$env:USERPROFILE\m3-recon-out")) { if (Test-Path (Join-Path $d "m2_sol_recon.json")) { $extra += @("--prior", $d) } }
if ($Resume -or $Attach -gt 0) { $extra += @("--attach", "$Attach") }
if ($Diagnose) { $extra += "--diagnose" }
if ($Public) { $extra += "--public-only" }
if ($EntryUrl) { $extra += @("--entry-url", $EntryUrl) }
# Si se entregan comandos por tuberia (automatizacion/pruebas), se reenvian al reconocedor; interactivo normal: usa el teclado.
if ($MyInvocation.ExpectingInput) { $input | & $py (Join-Path $repo "scripts\m3_sol_recon.py") --out $Out @extra } else { & $py (Join-Path $repo "scripts\m3_sol_recon.py") --out $Out @extra }
exit $LASTEXITCODE
