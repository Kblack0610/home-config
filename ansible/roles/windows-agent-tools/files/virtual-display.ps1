<#
.SYNOPSIS
Turn the virtual test monitor on or off, or show the monitors, from an SSH
session.

.DESCRIPTION
The windows-agent-tools role installs a software-only monitor (Virtual Display
Driver, hardware ID Root\MttVDD) and leaves it off. -On plugs it in at the
role's resolution and the given scale, and prints its name (\\.\DISPLAYn), so a
game can run and be screenshotted on it while the TV keeps showing what is on
it. -Off unplugs it.

-TvOff also stops the PC sending a picture to the TV until -Off. Use it when
the TV is on another source: a test keeps the displays awake, and a TV with
automatic source switching can jump to the PC's input when the PC's picture
comes back. Do not use it while someone watches the PC on the TV.

.EXAMPLE
C:\tools\virtual-display.ps1 -On -TvOff

.EXAMPLE
C:\tools\virtual-display.ps1 -Off
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'On')][switch]$On,
    [Parameter(ParameterSetName = 'On')][int]$Scale = 300,
    [Parameter(ParameterSetName = 'On')][switch]$TvOff,
    [Parameter(Mandatory = $true, ParameterSetName = 'Off')][switch]$Off
)
$ErrorActionPreference = 'Stop'

$HardwareId = 'Root\MttVDD'
$device = Get-PnpDevice -Class Display -EA 0 | Where-Object { $_.HardwareID -contains $HardwareId }
if (-not $device) { throw "There is no $HardwareId device. Apply the windows-agent-tools role (docs/windows-machines.md)." }

function Invoke-Session([string]$Arguments) {
    $session = Join-Path $PSScriptRoot 'virtual-display-session.ps1'
    & (Join-Path $PSScriptRoot 'run-on-desktop.ps1') -Path 'powershell.exe' -Wait -TimeoutSec 90 `
        -Arguments "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$session`" $Arguments"
    if ($LASTEXITCODE -ne 0) { throw "virtual-display-session.ps1 $Arguments failed with exit code $LASTEXITCODE" }
}

switch ($PSCmdlet.ParameterSetName) {
    'On' {
        if ($device.Status -ne 'OK') { $device | Enable-PnpDevice -Confirm:$false }
        Invoke-Session ("-Mode On -Scale $Scale" + $(if ($TvOff) { ' -TvOff' } else { '' }))
    }
    'Off' {
        if ($device.Status -eq 'OK') {
            Invoke-Session '-Mode Off'
            $device | Disable-PnpDevice -Confirm:$false
        }
        & (Join-Path $PSScriptRoot 'display-info.ps1')
    }
    'Status' {
        "virtual monitor: $(if ($device.Status -eq 'OK') { 'on' } else { 'off' })"
        & (Join-Path $PSScriptRoot 'display-info.ps1')
    }
}
