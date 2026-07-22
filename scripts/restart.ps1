<#
.SYNOPSIS
    Reinicia el stack (equivalente a stop.ps1 + start.ps1).
#>

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

& (Join-Path $ScriptDir "stop.ps1")
& (Join-Path $ScriptDir "start.ps1")
