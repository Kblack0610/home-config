# Runs IN the desktop session (see display-info.ps1). Physical pixels come from
# a DPI-aware process; the scale is the monitor's effective DPI over 96.
$ErrorActionPreference = 'Stop'
Add-Type -Namespace Win32 -Name Display -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("shcore.dll")] public static extern int GetDpiForMonitor(IntPtr hmonitor, int dpiType, out uint dpiX, out uint dpiY);
[DllImport("user32.dll")] public static extern IntPtr MonitorFromPoint(System.Drawing.Point pt, uint flags);
'@ -ReferencedAssemblies System.Drawing
[Win32.Display]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
    $center = New-Object System.Drawing.Point ($s.Bounds.X + [int]($s.Bounds.Width / 2)), ($s.Bounds.Y + [int]($s.Bounds.Height / 2))
    $mon = [Win32.Display]::MonitorFromPoint($center, 2)
    [uint32]$dx = 0; [uint32]$dy = 0
    [Win32.Display]::GetDpiForMonitor($mon, 0, [ref]$dx, [ref]$dy) | Out-Null
    "{0} {1}x{2} scale {3}%{4}" -f $s.DeviceName, $s.Bounds.Width, $s.Bounds.Height, [int]($dx * 100 / 96), $(if ($s.Primary) { ' primary' } else { '' })
}
