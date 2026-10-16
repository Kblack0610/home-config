# Started by the playtest-launch scheduled task inside the signed-in desktop
# session (an SSH session is session 0 and cannot draw on the screen).
# launch-target.txt holds the full path of the exe to start; the push script
# writes it before it runs the task.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = (Get-Content -LiteralPath (Join-Path $root 'launch-target.txt') -EA Stop | Select-Object -First 1).Trim()
if (-not (Test-Path -LiteralPath $target)) { throw "launch target missing: $target" }
$name = [IO.Path]::GetFileNameWithoutExtension($target)
Get-Process -Name $name -EA 0 | Stop-Process -Force
Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target)
