# windows-agent-tools

Makes a Windows PC drivable by an agent over SSH:

- SSH sessions get PowerShell as their shell (`HKLM:\SOFTWARE\OpenSSH\DefaultShell`), so `ssh <host> '<PowerShell>'` works with no wrapper. The inventory sets `ansible_shell_type: powershell` to match.
- Execution policy RemoteSigned for the machine. Client Windows ships Restricted, which refuses every `.ps1`.
- Helpers in `C:\tools`:
  - `run-on-desktop.ps1 -Path <exe> [-Arguments ..] [-Wait] [-TimeoutSec N]` runs a program in the logged-on user's desktop session through a one-shot Interactive scheduled task. Anything started straight from SSH runs in session 0, with no window on the screen and no display GPU. With `-Wait` it returns the program's output and exit code.
  - `screenshot.ps1` captures every monitor at physical resolution and prints the PNG's path under `C:\tools\runs\`, ready to `scp` back.
  - `display-info.ps1` prints each monitor's resolution and Windows scale %.
  - `virtual-display.ps1 -On [-Scale 300] [-TvOff]` / `-Off` plugs in or unplugs a software-only test monitor, so a game can be tested without taking over the TV. `run-on-desktop.ps1 -KeepDisplayOn` keeps the displays drawing while the program runs, and `screenshot.ps1 -Display <n>` captures one monitor.
- The virtual test monitor itself (`tasks/virtual-display.yml`, tag `virtual-display`): the pinned, checksummed Virtual Display Driver installed with nefcon, its signer (SignPath Foundation) added to the machine's TrustedPublisher store so the install needs no click, its settings written from `windows_agent_tools_vdd_resolutions`, and the device left off. A new pinned version is installed over the old one.

All of them need someone logged on at the PC; they say so when nobody is. Usage: `docs/windows-machines.md`, "Driving it from an agent".
