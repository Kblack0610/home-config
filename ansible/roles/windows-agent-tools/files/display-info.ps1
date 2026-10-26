<#
.SYNOPSIS
Print each monitor's resolution and Windows scale %, as the desktop session
sees them. Runs through run-on-desktop.ps1: from session 0 there is no display.
#>
$ErrorActionPreference = 'Stop'
$capture = Join-Path $PSScriptRoot 'display-info-session.ps1'
& (Join-Path $PSScriptRoot 'run-on-desktop.ps1') -Path 'powershell.exe' -Wait -TimeoutSec 60 `
    -Arguments "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$capture`""
exit $LASTEXITCODE
