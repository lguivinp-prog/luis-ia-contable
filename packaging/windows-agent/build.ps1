<#
.SYNOPSIS
  Construye el paquete del Agente SOL (Domain G): PyInstaller one-folder + Chromium propio + manifiesto SHA-256.
.DESCRIPTION
  Entorno de build: Windows 11 x64, Python 3.13.14 en el venv dedicado .venv-win-pkg (ver requirements-packaging.txt).
  La salida va a packaging\windows-agent\dist\ y build\ (ignorados por git). NO empaqueta venvs de desarrollo, pytest,
  fixtures, credenciales, identidades, material de CA, artefactos live_b ni scripts temporales.
  El paquete NO esta firmado (ver docs): verificar siempre el SHA-256 antes de instalar.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$BrowsersSource = "",
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path (Join-Path $here "..\..")).Path
if (-not $Version) { $Version = (Get-Content (Join-Path $here "VERSION") -Raw).Trim() }
if (-not $Python) { $Python = Join-Path $repo ".venv-win-pkg\Scripts\python.exe" }
if (-not $BrowsersSource) { $BrowsersSource = Join-Path $repo ".pw-browsers-win-e2" }
if (-not (Test-Path -LiteralPath $Python)) { throw "No existe el Python de build: $Python (cree .venv-win-pkg con requirements-packaging.txt)." }

$pyver = (& $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($pyver -ne "3.13.14") { throw "Python de build debe ser 3.13.14 (encontrado $pyver)." }
if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") { throw "Se requiere Windows x64 (AMD64)." }

$dist = Join-Path $here "dist"
$work = Join-Path $here "build"
$pyiOut = Join-Path $dist "_pyinstaller"
$pkgName = "luis-sol-agent-$Version-win-x64"
$pkgDir = Join-Path $dist $pkgName
foreach ($d in @($work, $pyiOut, $pkgDir)) { if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force } }
New-Item -ItemType Directory -Force -Path $dist | Out-Null

Write-Host "== PyInstaller (one-folder) =="
Push-Location $here
try {
    & $Python -m PyInstaller --noconfirm --clean --distpath $pyiOut --workpath $work "luis_sol_agent.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallo ($LASTEXITCODE)." }
} finally { Pop-Location }

New-Item -ItemType Directory -Force -Path $pkgDir | Out-Null
Copy-Item -LiteralPath (Join-Path $pyiOut "app") -Destination (Join-Path $pkgDir "app") -Recurse

Write-Host "== Chromium (motor de Playwright, propiedad de la app) =="
$browsersJson = & $Python -c "import json,pathlib,playwright; p=pathlib.Path(playwright.__file__).parent/'driver'/'package'/'browsers.json'; d=json.loads(p.read_text()); c=[b for b in d['browsers'] if b['name']=='chromium'][0]; print(json.dumps({'revision':c['revision'],'browserVersion':c.get('browserVersion','')}))"
$chromium = $browsersJson | ConvertFrom-Json
$chromiumDirName = "chromium-$($chromium.revision)"
$chromiumSrc = Join-Path $BrowsersSource $chromiumDirName
if (-not (Test-Path -LiteralPath $chromiumSrc)) { throw "Falta $chromiumSrc (instale con 'playwright install chromium' en un PLAYWRIGHT_BROWSERS_PATH de build)." }
$browsersDest = Join-Path $pkgDir "browsers"
New-Item -ItemType Directory -Force -Path $browsersDest | Out-Null
$chromiumDest = Join-Path $browsersDest $chromiumDirName
# Se excluye UNICAMENTE el instalador auxiliar de Chromium (`chrome-win64\setup.exe`): no se usa en tiempo de ejecucion
# (comprobado: version/browser-check/lanzamiento funcionan sin el) y Windows Defender lo elimina al extraer el ZIP, lo que
# rompe la verificacion del manifiesto. NO se excluye chrome.exe ni ningun otro .exe.
$setupExe = Join-Path $chromiumSrc "chrome-win64\setup.exe"
& robocopy.exe $chromiumSrc $chromiumDest /E /XF $setupExe /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy del Chromium fallo ($LASTEXITCODE)." }
$chromeExe = Join-Path $chromiumDest "chrome-win64\chrome.exe"
if (-not (Test-Path -LiteralPath $chromeExe)) { throw "Falta el ejecutable requerido del navegador: $chromeExe" }
if (Test-Path -LiteralPath (Join-Path $chromiumDest "chrome-win64\setup.exe")) { throw "setup.exe no debe estar en el paquete." }
$global:LASTEXITCODE = 0

Write-Host "== Metadatos de build =="
$commit = (& git -C $repo rev-parse HEAD).Trim()
$dirty = [bool](& git -C $repo status --porcelain)
$pyiVersion = (& $Python -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
$pwVersion = (& $Python -c "import importlib.metadata as m; print(m.version('playwright'))").Trim()
$info = [ordered]@{
    product = "luis-sol-agent"
    version = $Version
    build_id = "$Version+g$($commit.Substring(0,12))$(if ($dirty) { '.dirty' })"
    git_commit = $commit
    git_worktree_dirty = $dirty
    built_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    architecture = "win-x64"
    python_version = $pyver
    pyinstaller_version = $pyiVersion
    packaging_format = "pyinstaller-onedir"
    playwright_version = $pwVersion
    chromium_revision = $chromium.revision
    chromium_browser_version = $chromium.browserVersion
    signed = $false
    scope = "AUTHENTICATE_TO_LANDING_ONLY"
}
$infoJson = $info | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $pkgDir "build_info.json"), $infoJson, (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText((Join-Path $pkgDir "app\build_info.json"), $infoJson, (New-Object System.Text.UTF8Encoding($false)))
New-Item -ItemType Directory -Force -Path (Join-Path $pkgDir "scripts") | Out-Null
Copy-Item -LiteralPath (Join-Path $here "manage.ps1") -Destination (Join-Path $pkgDir "scripts\manage.ps1")

Write-Host "== Manifiesto SHA-256 =="
$manifestPath = Join-Path $pkgDir "MANIFEST.sha256"
$lines = Get-ChildItem -LiteralPath $pkgDir -Recurse -File |
    Where-Object { $_.FullName -ne $manifestPath } |
    Sort-Object FullName |
    ForEach-Object {
        $rel = $_.FullName.Substring($pkgDir.Length + 1).Replace("\", "/")
        "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $rel
    }
[System.IO.File]::WriteAllLines($manifestPath, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "== Zip =="
$zip = Join-Path $dist "$pkgName.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($pkgDir, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText("$zip.sha256", "$zipHash  $pkgName.zip`n", (New-Object System.Text.UTF8Encoding($false)))

$count = (Get-ChildItem -LiteralPath $pkgDir -Recurse -File).Count
$size = [math]::Round(((Get-ChildItem -LiteralPath $pkgDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "PACKAGE_DIR=$pkgDir"
Write-Host "PACKAGE_ZIP=$zip"
Write-Host "PACKAGE_SHA256=$zipHash"
Write-Host "PACKAGE_VERSION=$($info.build_id)"
Write-Host "PACKAGE_FILES=$count PACKAGE_SIZE_MB=$size"
Write-Host "WINDOWS_BINARY_SIGNED=NO"
