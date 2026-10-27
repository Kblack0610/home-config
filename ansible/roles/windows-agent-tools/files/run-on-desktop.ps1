<#
.SYNOPSIS
Run a program on the logged-on user's visible desktop, from an SSH session.

.DESCRIPTION
Anything started straight from SSH runs in session 0: it has no window on the
screen and no display GPU, so a game build launched that way is not what is on
the TV. This starts the program through a one-shot Interactive scheduled task
instead, which runs it in the console user's desktop session.

Without -Wait it returns as soon as the task has started the program (a game
keeps running). With -Wait it waits for the program to exit, prints anything it
wrote to stdout/stderr, and exits with its exit code.

With -KeepDisplayOn the displays stay on while the program runs (see
keep-display-on.ps1). Use it for anything you will screenshot: after the idle
timeout Windows stops drawing, and a screenshot returns an old frame.

.EXAMPLE
C:\tools\run-on-desktop.ps1 -Path C:\builds\Game\Game.exe -Arguments '-screen-fullscreen 1' -KeepDisplayOn

.EXAMPLE
C:\tools\run-on-desktop.ps1 -Path cmd.exe -Arguments '/c echo %SESSIONNAME%' -Wait
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$Arguments = '',
    [string]$WorkingDirectory = '',
    [switch]$Wait,
    [int]$TimeoutSec = 600,
    [switch]$KeepDisplayOn
)
$ErrorActionPreference = 'Stop'

# Task Scheduler has no completion event PowerShell can await (its operational
# log is off by default), so -Wait samples the task's LastTaskResult. One loop,
# one cadence.
$PollMs = 500
$NotYetRun = 267011   # SCHED_S_TASK_HAS_NOT_RUN
$Running = 267009     # SCHED_S_TASK_RUNNING

$user = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $user) { throw 'Nobody is logged on at the console, so there is no desktop to run on. Log in at the TV first.' }

$exe = if ([IO.Path]::IsPathRooted($Path)) { $Path } else { (Get-Command $Path).Source }
if (-not $WorkingDirectory) { $WorkingDirectory = Split-Path -Parent $exe }
$program = $exe
if ($KeepDisplayOn) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Arguments))
    $Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'keep-display-on.ps1')`" -Path `"$exe`"" +
        $(if ($Arguments) { " -ArgumentsBase64 $encoded" } else { '' })
    $exe = (Get-Command powershell.exe).Source
}

$runs = Join-Path $PSScriptRoot 'runs'
New-Item -ItemType Directory -Force $runs | Out-Null

# Tasks from earlier fire-and-forget runs are left registered (removing one can
# take its program down with it); sweep the ones that have finished.
Get-ScheduledTask -TaskName 'agent-desktop-*' -EA 0 | Where-Object State -ne 'Running' |
    Unregister-ScheduledTask -Confirm:$false

$name = 'agent-desktop-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$log = Join-Path $runs "$name.log"
if ($Wait) {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -WorkingDirectory $WorkingDirectory `
        -Argument "/c `"`"$exe`" $Arguments > `"$log`" 2>&1`""
} elseif ($Arguments) {
    $action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $WorkingDirectory -Argument $Arguments
} else {
    # -Argument refuses an empty string.
    $action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $WorkingDirectory
}
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name

if (-not $Wait) {
    Write-Output "started $program on $user's desktop (task $name)"
    exit 0
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
do {
    Start-Sleep -Milliseconds $PollMs
    $result = (Get-ScheduledTaskInfo -TaskName $name).LastTaskResult
    if ((Get-Date) -gt $deadline) {
        Stop-ScheduledTask -TaskName $name
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        throw "timed out after ${TimeoutSec}s: $exe is still running"
    }
} while ($result -eq $NotYetRun -or $result -eq $Running)

Unregister-ScheduledTask -TaskName $name -Confirm:$false
if (Test-Path $log) { Get-Content $log; Remove-Item $log }
exit $result
