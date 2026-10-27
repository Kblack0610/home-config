# Runs IN the desktop session (see virtual-display.ps1): which displays are
# active, and each one's scale, belong to the session, not to SSH's session 0.
param(
    [Parameter(Mandatory = $true)][ValidateSet('On', 'Off')][string]$Mode,
    [int]$Scale = 300,
    [switch]$TvOff,
    [int]$TimeoutSec = 30
)
$ErrorActionPreference = 'Stop'

# The monitor the driver plugs in reports EDID maker MTT, product 1337
# (Driver.cpp), so its device path holds this. The TV's holds SAM0DEE.
$VirtualId = '#MTT1337#'
$PollMs = 500

Add-Type -TypeDefinition @'
using System;
using System.Linq;
using System.Runtime.InteropServices;

namespace AgentTools {
    [StructLayout(LayoutKind.Sequential)] public struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)] public struct PathSource { public Luid AdapterId; public uint Id; public uint ModeIdx; public uint StatusFlags; }
    [StructLayout(LayoutKind.Sequential)] public struct PathTarget {
        public Luid AdapterId; public uint Id; public uint ModeIdx; public uint OutputTechnology; public uint Rotation; public uint Scaling;
        public uint RefreshNum; public uint RefreshDen; public uint ScanLineOrdering; public int TargetAvailable; public uint StatusFlags;
    }
    [StructLayout(LayoutKind.Sequential)] public struct PathInfo { public PathSource Source; public PathTarget Target; public uint Flags; }
    // DISPLAYCONFIG_MODE_INFO: 16-byte header, then a 48-byte union. Only the
    // source-mode position is read or written here.
    [StructLayout(LayoutKind.Explicit, Size = 64)] public struct ModeInfo {
        [FieldOffset(0)] public uint InfoType; [FieldOffset(4)] public uint Id; [FieldOffset(8)] public Luid AdapterId;
        [FieldOffset(28)] public int X; [FieldOffset(32)] public int Y;
    }
    [StructLayout(LayoutKind.Sequential)] public struct InfoHeader { public int Type; public uint Size; public Luid AdapterId; public uint Id; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] public struct TargetName {
        public InfoHeader Header; public uint Flags; public uint OutputTechnology; public ushort EdidManufactureId; public ushort EdidProductCodeId;
        public uint ConnectorInstance;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string FriendlyName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DevicePath;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] public struct SourceName {
        public InfoHeader Header; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string GdiName;
    }
    [StructLayout(LayoutKind.Sequential)] public struct DpiGet { public InfoHeader Header; public int Min; public int Cur; public int Max; }
    [StructLayout(LayoutKind.Sequential)] public struct DpiSet { public InfoHeader Header; public int Rel; }

    public static class Displays {
        const uint QdcOnlyActivePaths = 2;
        const uint SdcTopologyExtend = 0x4, SdcUseSuppliedDisplayConfig = 0x20, SdcApply = 0x80, SdcAllowChanges = 0x400;
        const uint PathActive = 1, ModeIdxInvalid = 0xffffffff, ModeTypeSource = 1;
        const int GetSourceName = 1, GetTargetName = 2, GetDpiScale = -3, SetDpiScale = -4;
        // The scales Windows offers, in its order. The DPI calls are undocumented
        // and take an offset from the monitor's recommended entry in this list.
        static readonly int[] Scales = { 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500 };

        [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint flags, out uint paths, out uint modes);
        [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint flags, ref uint numPaths, [Out] PathInfo[] paths, ref uint numModes, [Out] ModeInfo[] modes, IntPtr topology);
        [DllImport("user32.dll")] static extern int SetDisplayConfig(uint numPaths, PathInfo[] paths, uint numModes, ModeInfo[] modes, uint flags);
        [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref TargetName info);
        [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref SourceName info);
        [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref DpiGet info);
        [DllImport("user32.dll")] static extern int DisplayConfigSetDeviceInfo(ref DpiSet info);

        static void Check(int err, string what) { if (err != 0) throw new InvalidOperationException(what + " failed with Win32 error " + err); }

        public static PathInfo[] Active(out ModeInfo[] modes) {
            uint np, nm;
            Check(GetDisplayConfigBufferSizes(QdcOnlyActivePaths, out np, out nm), "GetDisplayConfigBufferSizes");
            var paths = new PathInfo[np]; modes = new ModeInfo[nm];
            Check(QueryDisplayConfig(QdcOnlyActivePaths, ref np, paths, ref nm, modes, IntPtr.Zero), "QueryDisplayConfig");
            Array.Resize(ref paths, (int)np); Array.Resize(ref modes, (int)nm);
            return paths;
        }

        public static string DevicePath(PathInfo p) {
            var n = new TargetName();
            n.Header.Type = GetTargetName; n.Header.Size = (uint)Marshal.SizeOf(typeof(TargetName));
            n.Header.AdapterId = p.Target.AdapterId; n.Header.Id = p.Target.Id;
            Check(DisplayConfigGetDeviceInfo(ref n), "DisplayConfigGetDeviceInfo(target name)");
            return n.DevicePath;
        }

        public static string GdiName(PathInfo p) {
            var n = new SourceName();
            n.Header.Type = GetSourceName; n.Header.Size = (uint)Marshal.SizeOf(typeof(SourceName));
            n.Header.AdapterId = p.Source.AdapterId; n.Header.Id = p.Source.Id;
            Check(DisplayConfigGetDeviceInfo(ref n), "DisplayConfigGetDeviceInfo(source name)");
            return n.GdiName;
        }

        static DpiGet Dpi(PathInfo p) {
            var g = new DpiGet();
            g.Header.Type = GetDpiScale; g.Header.Size = (uint)Marshal.SizeOf(typeof(DpiGet));
            g.Header.AdapterId = p.Source.AdapterId; g.Header.Id = p.Source.Id;
            Check(DisplayConfigGetDeviceInfo(ref g), "DisplayConfigGetDeviceInfo(dpi)");
            return g;
        }

        public static int Scale(PathInfo p) { var g = Dpi(p); return Scales[-g.Min + g.Cur]; }

        public static void SetScale(PathInfo p, int percent) {
            int idx = Array.IndexOf(Scales, percent);
            if (idx < 0) throw new ArgumentException("scale must be one of " + string.Join(", ", Scales));
            var g = Dpi(p);
            int recommended = -g.Min, rel = idx - recommended;
            if (rel > g.Max) throw new ArgumentException(percent + "% is above this monitor's largest scale, " + Scales[recommended + g.Max] + "%");
            var s = new DpiSet();
            s.Header.Type = SetDpiScale; s.Header.Size = (uint)Marshal.SizeOf(typeof(DpiSet));
            s.Header.AdapterId = p.Source.AdapterId; s.Header.Id = p.Source.Id; s.Rel = rel;
            Check(DisplayConfigSetDeviceInfo(ref s), "DisplayConfigSetDeviceInfo(dpi)");
        }

        // Make the path whose device path holds `keep` the only active display,
        // at the desktop origin so it can be primary. Not saved to the display
        // database, so the next plug-in of the monitor comes back extended.
        public static void OnlyActive(string keep) {
            ModeInfo[] modes;
            var paths = Active(out modes);
            foreach (var i in Enumerable.Range(0, paths.Length)) {
                if (DevicePath(paths[i]).Contains(keep)) {
                    var m = paths[i].Source.ModeIdx;
                    if (m != ModeIdxInvalid && modes[m].InfoType == ModeTypeSource) { modes[m].X = 0; modes[m].Y = 0; }
                } else {
                    paths[i].Flags &= ~PathActive;
                    paths[i].Source.ModeIdx = ModeIdxInvalid;
                    paths[i].Target.ModeIdx = ModeIdxInvalid;
                }
            }
            Check(SetDisplayConfig((uint)paths.Length, paths, (uint)modes.Length, modes,
                SdcApply | SdcUseSuppliedDisplayConfig | SdcAllowChanges), "SetDisplayConfig(only " + keep + ")");
        }

        public static void Extend() {
            Check(SetDisplayConfig(0, null, 0, null, SdcApply | SdcTopologyExtend), "SetDisplayConfig(extend)");
        }
    }
}
'@

function Get-Virtual { [AgentTools.Displays]::Active([ref]$null) | Where-Object { [AgentTools.Displays]::DevicePath($_).Contains($VirtualId) } }
function Test-TvActive { @([AgentTools.Displays]::Active([ref]$null) | Where-Object { -not [AgentTools.Displays]::DevicePath($_).Contains($VirtualId) }).Count -gt 0 }

if ($Mode -eq 'On') {
    # Windows plugs the monitor in a moment after the device starts. A script
    # with no window gets no display-change message to await, so sample it.
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not ($virtual = Get-Virtual)) {
        if ((Get-Date) -gt $deadline) { throw "the virtual monitor did not appear within ${TimeoutSec}s" }
        Start-Sleep -Milliseconds $PollMs
    }
    [AgentTools.Displays]::SetScale($virtual, $Scale)
    if ($TvOff) { [AgentTools.Displays]::OnlyActive($VirtualId) }
    elseif (-not (Test-TvActive)) { [AgentTools.Displays]::Extend() }
    "virtual monitor: {0}" -f [AgentTools.Displays]::GdiName((Get-Virtual))
} elseif (-not (Test-TvActive)) {
    # The TV is off for a test (-TvOff): bring it back before the virtual
    # monitor goes, so the desktop is never left without a screen.
    [AgentTools.Displays]::Extend()
}
& (Join-Path $PSScriptRoot 'display-info-session.ps1')
