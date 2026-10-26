# Windows Machines

How a Windows PC joins the fleet. The goal is one short visit to the PC, then everything else from the workstation over SSH.

What "on the fleet" means today: the PC pushes a liveness heartbeat every 60s to `fleet.kblab.me` and shows up on the fleet dashboard, Grafana and the status-bar glyph. Once SSH works it is also Ansible-managed (`windows_hosts` in `ansible/inventory.yml`, see [Managed by Ansible](#managed-by-ansible)). The heartbeat mechanism is shared with every other computer and is described in [device-fabric.md](./device-fabric.md) and `apps/gatus-fleet/README.md`.

## Inventory

| Name (fleet key) | Windows hostname | IP | User | Notes |
|------------------|------------------|----|------|-------|
| `windows-media` (`homelab_windows-media`) | `WINDOWS-MEDIA` | 192.168.1.8 (`windows-media-lan`) | `hughl` (Microsoft account, admin) | The living-room Windows and media box, and the infra Windows test host: agents drive it over SSH ([Driving it from an agent](#driving-it-from-an-agent)) and it is an opt-in runner ([Windows test runner](#windows-test-runner)). Ryzen 5 5500, 64 GB, RTX 3050, TV at 3840x2160 and 300% scale. Wi-Fi only (Intel AX210). Win10 Pro 22H2, legacy BIOS boot, SVM off. Enrolled 2026-10-16 as `living-room-pc` (`DESKTOP-BPLB04U`, .121), renamed 2026-10-26 |

## The path

### 1. Declare the name (workstation, one PR per repo)

Pick a kebab-case name for the machine, never a generic one like `windows`: one key per physical box, so a second Windows PC can never share (and mask) the first one's heartbeat. The name goes in four places:

1. `apps/gatus-fleet/config.yaml` - an `external-endpoints` entry, `group: homelab`.
2. `FLEET_ROSTER` in `apps/fleet-exporter/deployment.yaml`.
3. `FLEET_ROSTER` in `~/.config/fleet-pulse/env` (the `~/.dotfiles-private` repo, separate PR).
4. Optionally `FLEET_DISPLAY` in `~/.dotfiles/.config/waybar/fleet_pulse.sh`, if it should get its own labelled dot.

Check all four agree before merging:

```bash
python3 scripts/check-fleet-roster.py --roster-env ~/.config/fleet-pulse/env --display ~/.dotfiles/.config/waybar/fleet_pulse.sh
```

Merge the home-config PR on Forgejo and let Flux roll `gatus-fleet`. Until the PC enrolls, the exporter reports it as `fleet_machine_enrolled 0` and the glyph is amber. That is correct.

### 2. At the PC: one paste in an Administrator PowerShell

This is the only step that needs someone at the machine. It turns on the SSH server, trusts the keys on the GitHub account, and stops the PC and its network card from sleeping.

```powershell
# SSH server (also creates the firewall rule)
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic; Start-Service sshd

# Trust the workstation keys, no password needed
$k = irm https://github.com/Kblack0610.keys
Set-Content C:\ProgramData\ssh\administrators_authorized_keys $k -Encoding ascii
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
mkdir -Force $env:USERPROFILE\.ssh | Out-Null; Set-Content $env:USERPROFILE\.ssh\authorized_keys $k -Encoding ascii

# Stay reachable: no sleep on AC, Wi-Fi at max performance, network card never powered off
powercfg /change standby-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT 19cbb8fa-5279-450e-9fac-8a3d5fedd0c1 12bbebe6-58d6-4636-95bb-3217ef867c1a 0
powercfg /setactive SCHEME_CURRENT
$class = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
foreach ($a in Get-NetAdapter -Physical) { Get-ChildItem $class -EA 0 | ? { (Get-ItemProperty $_.PSPath -EA 0).NetCfgInstanceId -eq $a.InterfaceGuid } | % { Set-ItemProperty $_.PSPath PnPCapabilities 24 -Type DWord } }

# Report back
hostname; whoami; ipconfig | findstr IPv4
```

The window title must say Administrator, or the admin key file cannot be written and SSH keeps answering `Permission denied (publickey,...)`. Then from the workstation confirm `ssh <user>@<ip> whoami` works. The `PnPCapabilities` value (the "allow the computer to turn off this device" checkbox) only takes effect after a reboot, which step 3 does anyway.

### 3. Pin the IP (workstation)

Add a reservation to `infrastructure/dhcp/devices.yaml` (desktops go in `.30-.49`, media-only boxes in `.120-.139`, a PC that is also infra, like windows-media, next to the Macs in `.1-.9`), then `dhcp diff` and `dhcp --force sync` per `infrastructure/dhcp/README.md`. Use the MAC of the adapter that is actually `Up` in `Get-NetAdapter -Physical`; a PC on Wi-Fi has a different MAC from its unplugged ethernet port. The PC keeps its old address until its lease renews, so reboot it once from SSH: `shutdown /r /t 60 /c "..."` gives anyone at the screen a minute to cancel with `shutdown /a`.

### 4. Enroll over SSH (workstation)

`enroll.ps1` fetches the pusher, sends one probe heartbeat, and only registers the Scheduled Task if the probe got through. Feed it the token on stdin so it never lands on a command line or in shell history. Use `printf`, not `echo`: zsh's `echo` turns `\e` in a Windows path into an escape character.

```bash
PC=hughl@windows-media-lan NAME=windows-media
{ printf '$env:FLEET_TOKEN = "%s"\n' "$(cat ~/.config/fleet-pulse/token)"
  printf '%s\n' '$f = Join-Path $env:TEMP "enroll.ps1"' \
    'irm https://raw.githubusercontent.com/Kblack0610/.dotfiles/main/.local/src/fleet-pulse/enroll.ps1 -OutFile $f' \
    "& \$f -Name $NAME -Group homelab -Gatus https://fleet.kblab.me"
} | ssh "$PC" powershell -NoProfile -ExecutionPolicy Bypass -Command -
```

Then switch the task to run as S4U, so it reports whether or not anyone is logged in and never draws a console window on the screen:

```bash
printf '%s\n' 'Set-ScheduledTask fleet-pulse -Principal (New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited) | Out-Null' \
  'Start-ScheduledTask fleet-pulse' \
  '(Get-ScheduledTask fleet-pulse).Principal.LogonType' | ssh "$PC" powershell -NoProfile -Command -
```

### 5. Verify

```bash
curl -s https://fleet.kblab.me/api/v1/endpoints/statuses | jq -r '.[] | select(.key=="homelab_windows-media") | .results[-1] | "\(.timestamp) \(.success)"'
```

A timestamp under a minute old and `true`. Check again a few minutes after the S4U switch: the task's `LastTaskResult` is 0 even when a push fails (the pusher exits 0 by contract), so only fresh results in gatus prove the task is pushing.

## Gotchas

- **The PIN is not a password.** A Windows Hello PIN only unlocks the PC at the PC. SSH, Remote Desktop and file sharing want the account password, which on a Microsoft-account login is the Microsoft account password. Step 2 uses keys, so nobody needs it.
- **Admins ignore `~/.ssh/authorized_keys`.** For a user in Administrators, Windows OpenSSH reads only `C:\ProgramData\ssh\administrators_authorized_keys`, and only if its ACL is Administrators + SYSTEM. Step 2 writes both files so it works either way.
- **"No route to host" means the network card is asleep, not that the PC is off.** Nothing answered on the LAN at all. On 2026-10-16 the living-room PC (now windows-media) was on with the screen awake and still dropped off the network between checks. It is on Wi-Fi with Windows' default power saving, and the router had also logged a `169.254.x` fallback address for its MAC. Step 2 turns the Wi-Fi and network-card power saving off for this reason.
- **`powershell -Command -` reads stdin line by line.** One-line statements work; a multi-line `foreach { ... }` block silently produces nothing. Send anything multi-line as `-EncodedCommand $(iconv -t UTF-16LE script.ps1 | base64 -w0)`.
- **`Set-NetAdapterPowerManagement` has no `-AllowComputerToTurnOffDevice` parameter**, and `Disable-NetAdapterPowerManagement` leaves that checkbox on. The registry `PnPCapabilities = 24` in step 2 is what clears it.
- **Interactive vs S4U.** `enroll.ps1` registers the task as Interactive so it works without admin on a locked-down work machine. That flashes a console every minute and stops at logoff. On a home PC that is admin-controlled, S4U is the right setting (step 4).
- **A wrong name or group is a silent 404.** The gatus key is `<group>_<name>`. `enroll.ps1` probes first and stops on a 404, so a machine is never left installed but reporting nothing.

## Managed by Ansible

After step 4, add the PC to `windows_hosts` in `ansible/inventory.yml` (`ansible_host`, `ansible_user`, `windows_hostname`). Prove the connection with `ansible <name> -m ansible.windows.win_ping` before anything else.

The group sets `ansible_shell_type: powershell`, because `windows-agent-tools` makes PowerShell sshd's default shell. It works on a new PC whose sshd shell is still cmd too, so no override is needed.

Roles, bound in `ansible/playbooks/site.yml`:

- `windows-baseline` - the debloat: Store apps, ASUS/OEM software, telemetry and ads, OneDrive removal, autostart nags, power and network-card settings, hibernation off, gaming settings, cleanup, and the computer name. Every item is a list in its `defaults/main.yml`. Details and measured results: `ansible/roles/windows-baseline/README.md`.
- `windows-agent-tools` - PowerShell as the SSH shell, execution policy RemoteSigned, and the `C:\tools` desktop helpers. See [Driving it from an agent](#driving-it-from-an-agent).
- `github-actions-runner-windows` (windows-media only) - the opt-in test runner. See [Windows test runner](#windows-test-runner).

```bash
cd ansible && ansible-galaxy collection install -r requirements.yml -p ./collections
ansible-playbook playbooks/site.yml --limit windows-media --tags windows --check --diff
ansible-playbook playbooks/site.yml --limit windows-media --tags windows
ansible windows-media -m ansible.windows.win_reboot -a "pre_reboot_delay=60"   # first apply, or after a rename
```

Not wired into the in-cluster `ansible-runner` drift check: its image has no `ansible.windows` collection and its key is not on the PC.

## Driving it from an agent

SSH is the way in: `ssh hughl@windows-media-lan '<PowerShell>'`. The workstation key is trusted and `hughl` is an admin, so an agent has full control of the machine. For anything multi-line, send `-EncodedCommand` (see the gotcha below):

```bash
enc() { iconv -t UTF-16LE | base64 -w0; }
ssh hughl@windows-media-lan "powershell -NoProfile -EncodedCommand $(enc < script.ps1)"
```

An SSH session is Windows session 0. A program started from it has no window on the screen and no display GPU, so it is not what you would see on the TV. To test anything visual, use the helpers in `C:\tools`, which go through a one-shot Interactive scheduled task into the logged-on user's desktop (session 1):

| Command | What it does |
|---|---|
| `C:\tools\run-on-desktop.ps1 -Path <exe> [-Arguments '..'] [-WorkingDirectory ..]` | Starts the program on the TV desktop and returns once it is running. |
| `... -Wait [-TimeoutSec 600]` | Waits for it to exit, prints its stdout/stderr, exits with its exit code. |
| `C:\tools\screenshot.ps1` | Captures every monitor at physical resolution, prints the PNG path under `C:\tools\runs\`. |
| `C:\tools\display-info.ps1` | Prints each monitor's resolution and scale, e.g. `\\.\DISPLAY1 3840x2160 scale 300% primary`. |

```bash
ssh hughl@windows-media-lan 'C:\tools\run-on-desktop.ps1 -Path C:\bnbtest\windows\Game.exe -Arguments "-screen-fullscreen 1"'
ssh hughl@windows-media-lan 'C:\tools\screenshot.ps1'        # prints C:\tools\runs\screen-<stamp>.png
scp 'hughl@windows-media-lan:C:/tools/runs/screen-<stamp>.png' .
ssh hughl@windows-media-lan 'Stop-Process -Name Game'
```

Things to know:

- **Someone must be logged on.** The helpers and the runner need a desktop session, and there is no autologon (it would need the Microsoft account password stored on the PC). After a reboot, log in at the TV once. The helpers say so when nobody is logged on.
- **New windows can open behind the foreground app.** Windows' focus-stealing prevention applies to programs a task starts. A fullscreen game normally takes over; if a screenshot shows something else in front, minimize everything first: `run-on-desktop.ps1 -Path powershell.exe -Arguments '-NoProfile -Command (New-Object -ComObject Shell.Application).MinimizeAll()' -Wait`.
- **A blank or stale screenshot usually means the TV is off** or on another input. That is a human step.
- **Put test builds under `C:\bnbtest\`** and delete them when done. C: is a 1 TB disk with about 38 GB free (2026-10-26).
- **The host key.** Agents cannot write `~/.ssh/known_hosts` on the workstation. After the IP or name changes, the user accepts the new entry once: `ssh -o StrictHostKeyChecking=accept-new hughl@windows-media-lan exit`.

## Windows test runner

windows-media is an org-scoped GitHub Actions runner for Windows-only issues, the way mac-mini is for macOS. It is opt-in and never a merge gate: its labels are the runner's own `self-hosted`, `Windows`, `X64` plus `host-windows-media`, and as of 2026-10-26 no CI workflow asks for any of those, so a job lands here only when it says so:

```yaml
jobs:
  windows-check:
    runs-on: [self-hosted, windows]
    steps:
      - run: systeminfo | findstr /B /C:"OS Name" /C:"Host Name"
```

- It runs in hughl's desktop session (scheduled task `actions-runner`, started at logon), not as a Windows service, so job steps can open windows on the TV and use the GPU. It is offline whenever nobody is logged on.
- Every job step runs at below-normal priority (Task Scheduler priority 7, for CPU, I/O and memory), so what is on the TV wins.
- Not part of the Unity pool, and there is no Unity editor on the PC: Windows players are cross-built on the Linux pool (dodginballs `build-windows`), and this box runs them.
- Role: `ansible/roles/github-actions-runner-windows`. It counts in `apps/github-runner-exporter` as `windows-media:windows:1`.

## Windows 10 end of support

windows-media runs Windows 10 22H2. Consumer Extended Security Updates ended 2026-10-13, so it no longer gets security patches; Defender definitions still update. The CPU and TPM qualify for Windows 11, but the PC boots in legacy BIOS mode with SVM (CPU virtualization) off. One visit to the BIOS (CSM off, UEFI and Secure Boot on, SVM on) followed by `mbr2gpt /convert /allowFullOS` over SSH unlocks both an in-place Windows 11 upgrade and WSL2. Until then neither is possible.
