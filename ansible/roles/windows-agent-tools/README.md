# windows-agent-tools

Makes a Windows PC drivable by an agent over SSH:

- SSH sessions get PowerShell as their shell (`HKLM:\SOFTWARE\OpenSSH\DefaultShell`), so `ssh <host> '<PowerShell>'` works with no wrapper. The inventory sets `ansible_shell_type: powershell` to match.
- Execution policy RemoteSigned for the machine. Client Windows ships Restricted, which refuses every `.ps1`.
- Helpers in `C:\tools`:
  - `run-on-desktop.ps1 -Path <exe> [-Arguments ..] [-Wait] [-TimeoutSec N]` runs a program in the logged-on user's desktop session through a one-shot Interactive scheduled task. Anything started straight from SSH runs in session 0, with no window on the screen and no display GPU. With `-Wait` it returns the program's output and exit code.
  - `screenshot.ps1` captures every monitor at physical resolution and prints the PNG's path under `C:\tools\runs\`, ready to `scp` back.
  - `display-info.ps1` prints each monitor's resolution and Windows scale %.

All three need someone logged on at the PC; they say so when nobody is. Usage: `docs/windows-machines.md`, "Driving it from an agent".
