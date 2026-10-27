# Runs IN the desktop session (see run-on-desktop.ps1 -KeepDisplayOn): runs a
# program and holds the displays on until it exits. Windows turns every monitor
# off together after the idle timeout, virtual ones included, and a monitor that
# is off is not drawn, so a screenshot returns the last frame from before.
param(
    [Parameter(Mandatory = $true)][string]$Path,
    # Base64 of the UTF-16 argument string, so quotes survive the scheduled task.
    [string]$ArgumentsBase64 = ''
)
$ErrorActionPreference = 'Stop'

Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);'
$EsContinuous = [uint32]2147483648   # 0x80000000
$EsSystemRequired = [uint32]1
$EsDisplayRequired = [uint32]2
if ([Win32.Power]::SetThreadExecutionState($EsContinuous -bor $EsSystemRequired -bor $EsDisplayRequired) -eq 0) {
    throw 'SetThreadExecutionState refused the display request'
}

$start = @{ FilePath = $Path; NoNewWindow = $true; PassThru = $true }
if ($ArgumentsBase64) { $start.ArgumentList = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($ArgumentsBase64)) }
$p = Start-Process @start
# Without touching Handle now, ExitCode reads empty after the exit.
$null = $p.Handle
# The request lasts as long as this thread; it ends when the program does.
$p.WaitForExit()
exit $p.ExitCode
