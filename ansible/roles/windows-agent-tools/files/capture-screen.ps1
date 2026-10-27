<#
.SYNOPSIS
Save the whole desktop (every monitor), or one monitor, to a PNG. Runs IN the
desktop session; call screenshot.ps1 from SSH, which starts this through
run-on-desktop.ps1.
#>
param(
    [Parameter(Mandatory = $true)][string]$Out,
    # n of \\.\DISPLAYn, as display-info.ps1 and virtual-display.ps1 print it. 0 = all.
    [int]$Display = 0
)
$ErrorActionPreference = 'Stop'

# Without this a scaled display (e.g. 150% on a TV) is captured at the scaled-
# down logical size, and the picture shows only part of the screen.
Add-Type -Namespace Win32 -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[Win32.Dpi]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
if ($Display) {
    $screen = [System.Windows.Forms.Screen]::AllScreens | Where-Object DeviceName -eq "\\.\DISPLAY$Display"
    if (-not $screen) { throw "no \\.\DISPLAY$Display; the monitors are $(([System.Windows.Forms.Screen]::AllScreens).DeviceName -join ', ')" }
    $b = $screen.Bounds
}
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
