<#
.SYNOPSIS
  Instala / opera / actualiza / desinstala el Agente SOL de Windows (Domain G). Aplicacion INTERACTIVA POR USUARIO.
.DESCRIPTION
  El Agente NO es un servicio de Windows (necesita Chromium visible y supervision humana en el escritorio
  interactivo). El autoarranque es una tarea de Task Scheduler "solo cuando el usuario ha iniciado sesion".

  Separacion estricta (el upgrade/uninstall NUNCA tocan la identidad):
    %LOCALAPPDATA%\LuisIAContable\SolAgentApp\  app\ + browsers\ + build_info.json + MANIFEST.sha256   (binarios)
    %LOCALAPPDATA%\LuisIAContable\SolAgent\     identidad (DPAPI) + configuracion + logs + broker_ca\ca.cert.pem

  Acciones: install | status | start | stop | upgrade | uninstall | purge | set-ca (copia el certificado PUBLICO de la CA)
  Ninguna accion imprime claves privadas, secretos de boveda ni credenciales SOL; ninguna toca datos de credenciales.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("install", "status", "start", "stop", "upgrade", "uninstall", "purge", "set-ca")]
    [string]$Action,
    [string]$PackageDir = "",
    [string]$CaCertificate = "",
    [switch]$DisableSolAuth,
    [switch]$Start,
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [string]$ConfirmPurge = "",
    [string]$Root = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $env:LOCALAPPDATA) { throw "LOCALAPPDATA no esta definida." }
$DefaultRoot = Join-Path $env:LOCALAPPDATA "LuisIAContable"
if (-not $Root) { $Root = $DefaultRoot }
$CustomRoot = $Root.TrimEnd("\") -ne $DefaultRoot
$AppRoot = Join-Path $Root "SolAgentApp"
$AppDir = Join-Path $AppRoot "app"
$BrowsersDir = Join-Path $AppRoot "browsers"
$DataDir = Join-Path $Root "SolAgent"
$TaskPath = "\LuisIAContable\"
$TaskName = "SolAgent"
if ($CustomRoot) {
    $sha = [System.Security.Cryptography.SHA1]::Create()
    $TaskName = "SolAgent-" + (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant())) | Select-Object -First 4 | ForEach-Object { $_.ToString("x2") }) -join "")
}
$WindowedExe = Join-Path $AppDir "luis-sol-agent-w.exe"
$ConsoleExe = Join-Path $AppDir "luis-sol-agent.exe"
$ProcessNames = @("luis-sol-agent-w", "luis-sol-agent")
$PurgePhrase = "DELETE-AGENT-IDENTITY"
$DataArgs = @()
if ($CustomRoot) { $DataArgs = @("--data-dir", $DataDir) }

function Write-Kv([string]$k, $v) { Write-Host ("{0}={1}" -f $k, $v) }

function Test-Manifest([string]$dir) {
    $manifest = Join-Path $dir "MANIFEST.sha256"
    if (-not (Test-Path -LiteralPath $manifest)) { throw "Falta MANIFEST.sha256 en el paquete: $dir" }
    $expected = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($manifest)) {
        if (-not $line.Trim()) { continue }
        $hash, $rel = $line -split "  ", 2
        $expected[$rel] = $hash
    }
    $checked = 0
    foreach ($rel in $expected.Keys) {
        $path = Join-Path $dir ($rel.Replace("/", "\"))
        if (-not (Test-Path -LiteralPath $path)) { throw "Archivo del manifiesto ausente: $rel" }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected[$rel]) { throw "SHA-256 no coincide para $rel (paquete alterado o corrupto)." }
        $checked++
    }
    $manifestFull = (Resolve-Path -LiteralPath $manifest).Path
    $actualFiles = @(Get-ChildItem -LiteralPath $dir -Recurse -File | Where-Object { $_.FullName -ne $manifestFull }).Count
    if ($actualFiles -ne $expected.Count) { throw "El paquete contiene archivos fuera del manifiesto ($actualFiles vs $($expected.Count))." }
    Write-Kv "MANIFEST_VERIFIED_FILES" $checked
}

function Get-AgentProcesses {
    $appRootFull = [System.IO.Path]::GetFullPath($AppRoot)
    foreach ($n in $ProcessNames) {
        Get-Process -Name $n -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -and $_.Path.StartsWith($appRootFull, [System.StringComparison]::OrdinalIgnoreCase) } catch { $false }
        }
    }
}

function Stop-Agent {
    $stopped = 0
    $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq "Running") { Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName }
    foreach ($p in @(Get-AgentProcesses)) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        $p.WaitForExit(10000) | Out-Null
        $stopped++
    }
    Write-Kv "AGENT_PROCESSES_STOPPED" $stopped
}

function Invoke-WithRetry([scriptblock]$op, [string]$what) {
    # Tras detener el Agente, Windows (loader/antivirus) puede retener unos segundos handles sobre los binarios.
    for ($i = 1; $i -le 20; $i++) {
        try { & $op; return } catch {
            if ($i -eq 20) { throw "No se pudo $what tras reintentos: $($_.Exception.Message)" }
            Start-Sleep -Milliseconds 750
        }
    }
}

function Protect-Directory([string]$dir) {
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $dir /inheritance:r /grant:r "${user}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning "No se pudo restringir la ACL de $dir (la identidad sigue protegida por DPAPI)." }
}

function Install-CaCertificate([string]$source) {
    if (-not $source) { return }
    if (-not (Test-Path -LiteralPath $source)) { throw "No existe el certificado publico: $source" }
    $text = [System.IO.File]::ReadAllText($source)
    if ($text -match "PRIVATE KEY") { throw "El archivo contiene una CLAVE PRIVADA; solo se acepta el certificado publico (ca.cert.pem)." }
    if ($text -notmatch "BEGIN CERTIFICATE") { throw "El archivo no es un certificado PEM." }
    $caDir = Join-Path $DataDir "broker_ca"
    New-Item -ItemType Directory -Force -Path $caDir | Out-Null
    Copy-Item -LiteralPath $source -Destination (Join-Path $caDir "ca.cert.pem") -Force
}

function Register-AgentTask {
    $argument = "run"
    if (-not $DisableSolAuth) { $argument = "run --enable-sol-auth" }
    if ($CustomRoot) { $argument = "--data-dir `"$DataDir`" $argument" }
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $WindowedExe -Argument $argument -WorkingDirectory $AppDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -Description "Luis IA Contable - Agente SOL (interactivo por usuario)" | Out-Null
    Write-Kv "AUTOSTART_TASK" "$TaskPath$TaskName (AtLogOn, $user, Interactive, Limited)"
    Write-Kv "TASK_ARGUMENTS" $argument
}

function Install-Files([string]$pkg) {
    New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $pkg "app") -Destination $AppDir -Recurse
    New-Item -ItemType Directory -Force -Path $BrowsersDir | Out-Null
    foreach ($b in Get-ChildItem -LiteralPath (Join-Path $pkg "browsers") -Directory) {
        $target = Join-Path $BrowsersDir $b.Name
        if (-not (Test-Path -LiteralPath $target)) { Copy-Item -LiteralPath $b.FullName -Destination $target -Recurse }
    }
    Copy-Item -LiteralPath (Join-Path $pkg "build_info.json") -Destination (Join-Path $AppRoot "build_info.json") -Force
    Copy-Item -LiteralPath (Join-Path $pkg "MANIFEST.sha256") -Destination (Join-Path $AppRoot "MANIFEST.sha256") -Force
}

switch ($Action) {
    "install" {
        if (-not $PackageDir) { throw "install requiere -PackageDir." }
        $PackageDir = (Resolve-Path -LiteralPath $PackageDir).Path
        if (Test-Path -LiteralPath $AppDir) { throw "Ya esta instalado en $AppRoot. Use 'upgrade' (la identidad se conserva)." }
        Test-Manifest $PackageDir
        Install-Files $PackageDir
        New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
        Protect-Directory $DataDir
        Install-CaCertificate $CaCertificate
        Register-AgentTask
        Write-Kv "INSTALLED_APP_DIR" $AppDir
        Write-Kv "AGENT_DATA_DIR" $DataDir
        Write-Kv "WINDOWS_SERVICE_REGISTERED" "NO"
        if ($Start) { Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName; Write-Kv "STARTED" "YES" }
    }
    "upgrade" {
        if (-not $PackageDir) { throw "upgrade requiere -PackageDir." }
        $PackageDir = (Resolve-Path -LiteralPath $PackageDir).Path
        if (-not (Test-Path -LiteralPath $AppDir)) { throw "No hay instalacion previa; use 'install'." }
        Test-Manifest $PackageDir
        $wasRunning = @(Get-AgentProcesses).Count -gt 0
        Stop-Agent
        $previous = Join-Path $AppRoot "app.previous"
        if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous -Recurse -Force }
        Invoke-WithRetry { Move-Item -LiteralPath $AppDir -Destination $previous -ErrorAction Stop } "mover la version anterior"
        try {
            Copy-Item -LiteralPath (Join-Path $PackageDir "app") -Destination $AppDir -Recurse
            foreach ($b in Get-ChildItem -LiteralPath (Join-Path $PackageDir "browsers") -Directory) {
                $target = Join-Path $BrowsersDir $b.Name
                if (-not (Test-Path -LiteralPath $target)) { Copy-Item -LiteralPath $b.FullName -Destination $target -Recurse }
            }
            Copy-Item -LiteralPath (Join-Path $PackageDir "build_info.json") -Destination (Join-Path $AppRoot "build_info.json") -Force
            Copy-Item -LiteralPath (Join-Path $PackageDir "MANIFEST.sha256") -Destination (Join-Path $AppRoot "MANIFEST.sha256") -Force
            & $ConsoleExe @DataArgs version | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "El ejecutable nuevo no responde." }
        } catch {
            Write-Warning "Fallo el upgrade; restaurando la version anterior: $($_.Exception.Message)"
            if (Test-Path -LiteralPath $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
            Move-Item -LiteralPath $previous -Destination $AppDir
            throw
        }
        Install-CaCertificate $CaCertificate
        Register-AgentTask
        Write-Kv "IDENTITY_TOUCHED_BY_UPGRADE" "NO"
        Write-Kv "PREVIOUS_VERSION_KEPT_AT" $previous
        if ($wasRunning -or $Start) { Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName; Write-Kv "RESTARTED" "YES" }
    }
    "start" {
        if (-not (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue)) { throw "No instalado." }
        if (@(Get-AgentProcesses).Count -gt 0) { Write-Kv "ALREADY_RUNNING" "YES"; return }
        Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
        Write-Kv "STARTED" "YES"
    }
    "stop" { Stop-Agent }
    "set-ca" {
        if (-not $CaCertificate) { throw "set-ca requiere -CaCertificate (ca.cert.pem publico)." }
        New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
        Install-CaCertificate $CaCertificate
        Write-Kv "CA_CERTIFICATE_INSTALLED" (Join-Path (Join-Path $DataDir "broker_ca") "ca.cert.pem")
    }
    "uninstall" {
        Stop-Agent
        $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) { Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false }
        if (Test-Path -LiteralPath $AppRoot) { Invoke-WithRetry { Remove-Item -LiteralPath $AppRoot -Recurse -Force -ErrorAction Stop } "eliminar los binarios" }
        Write-Kv "AUTOSTART_REMOVED" "YES"
        Write-Kv "BINARIES_REMOVED" "YES"
        Write-Kv "AGENT_IDENTITY_PRESERVED" $(if (Test-Path -LiteralPath $DataDir) { "YES" } else { "NO_IDENTITY_FOUND" })
    }
    "purge" {
        if ($ConfirmPurge -ne $PurgePhrase) {
            throw "purge BORRA la identidad del Agente (no se puede deshacer; habra que re-emparejar y revocar el agente en el backend). Repita con -ConfirmPurge $PurgePhrase"
        }
        if (@(Get-AgentProcesses).Count -gt 0) { throw "Detenga el Agente antes de purgar (stop)." }
        if (Test-Path -LiteralPath $DataDir) { Remove-Item -LiteralPath $DataDir -Recurse -Force }
        Write-Kv "AGENT_IDENTITY_PURGED" "YES"
        Write-Host "Recuerde revocar este agente en el backend (la identidad local ya no existe)."
    }
    "status" {
        $installed = Test-Path -LiteralPath $AppDir
        Write-Kv "INSTALLED" $(if ($installed) { "YES" } else { "NO" })
        Write-Kv "WINDOWS_SERVICE" "NO (aplicacion interactiva por usuario)"
        $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $infoT = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName
            Write-Kv "AUTOSTART_TASK" "PRESENT state=$($task.State) lastResult=$($infoT.LastTaskResult)"
            Write-Kv "TASK_RUN_AS" "$($task.Principal.UserId) logon=$($task.Principal.LogonType) runLevel=$($task.Principal.RunLevel)"
        } else { Write-Kv "AUTOSTART_TASK" "ABSENT" }
        $procs = @(Get-AgentProcesses)
        Write-Kv "AGENT_PROCESS_RUNNING" $(if ($procs.Count -gt 0) { "YES" } else { "NO" })
        foreach ($p in $procs) { Write-Kv "AGENT_PROCESS" "pid=$($p.Id) session=$($p.SessionId) name=$($p.ProcessName)" }
        Write-Kv "AGENT_DATA_DIR_PRESENT" $(if (Test-Path -LiteralPath $DataDir) { "YES" } else { "NO" })
        Write-Kv "CA_CERTIFICATE_PRESENT" $(if (Test-Path -LiteralPath (Join-Path $DataDir "broker_ca\ca.cert.pem")) { "YES" } else { "NO" })
        $logFile = Join-Path $DataDir "logs\agent.log"
        if (Test-Path -LiteralPath $logFile) { Write-Kv "AGENT_LOG" "$logFile ($([math]::Round((Get-Item -LiteralPath $logFile).Length/1KB,1)) KB)" }
        if ($installed) {
            Write-Host "-- agente (exe status) --"
            & $ConsoleExe @DataArgs version
            & $ConsoleExe @DataArgs status
        }
        Write-Host "-- backend / broker --"
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 -Uri ($BackendUrl.TrimEnd("/") + "/api/v1/health/ready")
            Write-Kv "BACKEND_READY" "YES (HTTP $($r.StatusCode))"
        } catch { Write-Kv "BACKEND_READY" "NO" }
        foreach ($port in 8995, 8996) {
            $ok = $false
            try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $port); $ok = $true; $c.Close() } catch { }
            Write-Kv "BROKER_PORT_$port" $(if ($ok) { "LISTENING" } else { "CLOSED" })
        }
    }
}
