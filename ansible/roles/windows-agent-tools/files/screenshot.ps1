<#
.SYNOPSIS
Screenshot the logged-on desktop (what is on the TV) from an SSH session and
print the PNG's path, ready to scp back.

.EXAMPLE
ssh hughl@windows-media-lan 'C:\tools\screenshot.ps1'
scp 'hughl@windows-media-lan:C:/tools/runs/screen-20261026-221500.png' .
#>
param([string]$Out = (Join-Path (Join-Path $PSScriptRoot 'runs') ("screen-{0:yyyyMMdd-HHmmss}.png" -f (Get-Date))))
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force (Split-Path -Parent $Out) | Out-Null
$capture = Join-Path $PSScriptRoot 'capture-screen.ps1'
& (Join-Path $PSScriptRoot 'run-on-desktop.ps1') -Path 'powershell.exe' -Wait -TimeoutSec 60 `
    -Arguments "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$capture`" -Out `"$Out`""
if ($LASTEXITCODE -ne 0) { throw "capture failed with exit code $LASTEXITCODE" }
if (-not (Test-Path $Out)) { throw "capture exited 0 but wrote no $Out" }
$Out
