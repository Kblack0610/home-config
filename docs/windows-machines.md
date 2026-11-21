# Windows Machines

How a Windows PC joins the fleet. The goal is one short visit to the PC, then everything else from the workstation over SSH.

What "on the fleet" means today: the PC pushes a liveness heartbeat every 60s to `fleet.kblab.me` and shows up on the fleet dashboard, Grafana and the status-bar glyph. Once SSH works it is also Ansible-managed (`windows_hosts` in `ansible/inventory.yml`, see [Managed by Ansible](#managed-by-ansible)). The heartbeat mechanism is shared with every other computer and is described in [device-fabric.md](./device-fabric.md) and `apps/gatus-fleet/README.md`.

## Inventory

No Windows PC is on the fleet now: the last one, `windows-media`, became the Linux CI host `tuf-b550` on 2026-11-20 ([below](#windows-media-retired-2026-11-20)). A new PC adds its row here.

## The path

### 1. Declare the name (workstation, one PR per repo)

Pick a kebab-case name for the machine, never a generic one like `windows`: one key per physical box, so a second Windows PC can never share (and mask) the first one's heartbeat. The name goes in one place, the `fleet_pulse` group in `ansible/inventory.yml`: the host with `fleet_pulse_group: homelab` and `fleet_pulse_push: true` (and `fleet_pulse_display: <label>` if it should get its own labelled dot on the waybar). Then render and commit:

```bash
python3 scripts/render-fleet-pulse.py
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

Add a reservation to `infrastructure/dhcp/devices.yaml` (desktops go in `.30-.49`, media-only boxes in `.120-.139`, a PC that is also infra next to the Macs in `.1-.9`), then `dhcp diff` and `dhcp --force sync` per `infrastructure/dhcp/README.md`. Use the MAC of the adapter that is actually `Up` in `Get-NetAdapter -Physical`; a PC on Wi-Fi has a different MAC from its unplugged ethernet port. The PC keeps its old address until its lease renews, so reboot it once from SSH: `shutdown /r /t 60 /c "..."` gives anyone at the screen a minute to cancel with `shutdown /a`.

### 4. Enroll over SSH (workstation)

`enroll.ps1` fetches the pusher, sends one probe heartbeat, and only registers the Scheduled Task if the probe got through. Feed it the token on stdin so it never lands on a command line or in shell history. Use `printf`, not `echo`: zsh's `echo` turns `\e` in a Windows path into an escape character.

```bash
PC=<user>@<name>-lan NAME=<name>
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
curl -s https://fleet.kblab.me/api/v1/endpoints/statuses | jq -r '.[] | select(.key=="homelab_<name>") | .results[-1] | "\(.timestamp) \(.success)"'
```

A timestamp under a minute old and `true`. Check again a few minutes after the S4U switch: the task's `LastTaskResult` is 0 even when a push fails (the pusher exits 0 by contract), so only fresh results in gatus prove the task is pushing.

## Gotchas

- **The PIN is not a password.** A Windows Hello PIN only unlocks the PC at the PC. SSH, Remote Desktop and file sharing want the account password, which on a Microsoft-account login is the Microsoft account password. Step 2 uses keys, so nobody needs it.
- **Admins ignore `~/.ssh/authorized_keys`.** For a user in Administrators, Windows OpenSSH reads only `C:\ProgramData\ssh\administrators_authorized_keys`, and only if its ACL is Administrators + SYSTEM. Step 2 writes both files so it works either way.
- **"No route to host" means the network card is asleep, not that the PC is off.** Nothing answered on the LAN at all. On 2026-10-16 the living-room PC was on with the screen awake and still dropped off the network between checks. It is on Wi-Fi with Windows' default power saving, and the router had also logged a `169.254.x` fallback address for its MAC. Step 2 turns the Wi-Fi and network-card power saving off for this reason.
- **It does not answer ping.** The Windows firewall drops ICMP echo by default, so `ping` reads a healthy PC as down. Test reachability with `ssh $PC hostname` or check the router's ARP table for its MAC instead (`ip neigh | grep -i <mac>`). On 2026-10-26 a ping wait loop sat for ten minutes after the rename reboot while the PC was already up at .8.
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

```bash
cd ansible && ansible-galaxy collection install -r requirements.yml -p ./collections
ansible-playbook playbooks/site.yml --limit <name> --tags windows --check --diff
ansible-playbook playbooks/site.yml --limit <name> --tags windows
ansible <name> -m ansible.windows.win_reboot -a "pre_reboot_delay=60"   # first apply, or after a rename
```

Not wired into the in-cluster `ansible-runner` drift check: its image has no `ansible.windows` collection and its key is not on the PC.

## Driving it from an agent

SSH is the way in: `ssh $PC '<PowerShell>'` ($PC from step 4). The workstation key is trusted and the user is an admin, so an agent has full control of the machine. For anything multi-line, send `-EncodedCommand` (see the gotcha below):

```bash
enc() { iconv -t UTF-16LE | base64 -w0; }
ssh $PC "powershell -NoProfile -EncodedCommand $(enc < script.ps1)"
```

An SSH session is Windows session 0. A program started from it has no window on the screen and no display GPU, so it is not what you would see on the TV. To test anything visual, use the helpers in `C:\tools`, which go through a one-shot Interactive scheduled task into the logged-on user's desktop (session 1):

| Command | What it does |
|---|---|
| `C:\tools\run-on-desktop.ps1 -Path <exe> [-Arguments '..'] [-WorkingDirectory ..]` | Starts the program on the TV desktop and returns once it is running. |
| `... -Wait [-TimeoutSec 600]` | Waits for it to exit, prints its stdout/stderr, exits with its exit code. |
| `C:\tools\screenshot.ps1` | Captures every monitor at physical resolution, prints the PNG path under `C:\tools\runs\`. |
| `C:\tools\display-info.ps1` | Prints each monitor's resolution and scale, e.g. `\\.\DISPLAY1 3840x2160 scale 300% primary`. |
| `... -KeepDisplayOn` | Holds the displays on until the program exits. Use it for anything you will screenshot. |
| `C:\tools\screenshot.ps1 -Display <n>` | Captures only `\\.\DISPLAY<n>`. |
| `C:\tools\virtual-display.ps1 -On [-Scale 300] [-TvOff]` / `-Off` | Plugs in or unplugs the virtual test monitor. See [A test monitor off the TV](#a-test-monitor-off-the-tv). |

```bash
ssh $PC 'C:\tools\run-on-desktop.ps1 -Path C:\bnbtest\windows\Game.exe -Arguments "-screen-fullscreen 1"'
ssh $PC 'C:\tools\screenshot.ps1'        # prints C:\tools\runs\screen-<stamp>.png
scp "$PC:C:/tools/runs/screen-<stamp>.png" .
ssh $PC 'Stop-Process -Name Game'
```

Things to know:

- **Someone must be logged on.** The helpers need a desktop session. A PC with `windows_baseline_autologon` signs itself in at boot (see [Unattended restart](#unattended-restart)); on a PC without that, log in at the screen after a reboot. The helpers say so when nobody is logged on.
- **New windows can open behind the foreground app.** Windows' focus-stealing prevention applies to programs a task starts. A fullscreen game normally takes over; if a screenshot shows something else in front, minimize everything first: `run-on-desktop.ps1 -Path powershell.exe -Arguments '-NoProfile -Command (New-Object -ComObject Shell.Application).MinimizeAll()' -Wait`.
- **A stale screenshot means the displays are asleep.** The screen turns off after 15 minutes idle, and Windows stops drawing every monitor, virtual ones too, so a capture returns the last frame (2026-10-26: a capture showed the clock from 22 hours earlier). Start what you will screenshot with `-KeepDisplayOn`. The TV being off or on another input does not cause it: Windows keeps drawing.
- **Put test builds under `C:\bnbtest\`** and delete them when done.
- **The host key.** Agents cannot write `~/.ssh/known_hosts` on the workstation. After the IP or name changes, the user accepts the new entry once: `ssh -o StrictHostKeyChecking=accept-new $PC exit`.

### Unattended restart

A PC with no one at it (a TV PC) has to come back by itself after a reboot or a power cut. That needs three things:

| What | Set by | Check |
|---|---|---|
| Power on when mains power returns | Firmware, once, at the PC: **Del** at boot, **Advanced** -> **APM Configuration** -> **Restore AC Power Loss** -> **Power On**, then **F10** to save (ASUS TUF GAMING B550-PLUS WIFI II; [baremetal-power.md](baremetal-power.md)) | A controlled power pull: the PC boots and answers SSH |
| Sign in at boot | `windows_baseline_autologon: true` (inventory) makes it possible; then once, at the PC: **Win+R**, `netplwiz`, untick **Users must enter a user name and password to use this computer**, **Apply**, type the Microsoft account password twice | `ansible-playbook ... --tags autologon` passes; after a reboot `(Get-CimInstance Win32_ComputerSystem).UserName` is `<HOSTNAME>\<user>` |
| No sleep on AC | `windows-baseline` power tasks | Already applied |

- **The password stays out of this repo.** netplwiz stores it as an LSA secret on the PC. The role only unhides the netplwiz box (`DevicePasswordLessBuildVersion=0`, which Windows Hello sign-in had set to 2) and asserts `AutoAdminLogon=1`, failing with these steps when it is off.
- **The trade-off.** Whoever turns the PC on gets that user's desktop, an admin account. That suits a living-room PC; do not set `windows_baseline_autologon` on a PC that leaves the house.
- **Changing the Microsoft account password breaks it silently.** The stored copy goes stale and the PC stops at the sign-in screen. Repeat the netplwiz step.
- **The firmware setting is readable over SSH, but its values are not documented.** The board's ASUS WMI class answers `Invoke-CimMethod -InputObject @(Get-CimInstance -Namespace root\wmi ASUSManagement)[0] -MethodName GetOptionData -Arguments @{OptionName='LastState'}` (read 2026-10-26: `ErrorCode=0`, `Value=1`; unknown names return `ErrorCode=14`). `LastState` is most likely Restore AC Power Loss, but which number means **Power On** is unconfirmed, so set it in the firmware screen and never write it blind with `SetOptionData`. Once the screen has been read, record the mapping here.

### A test monitor off the TV

The TV is also the living room's TV. For a visual test, plug in the virtual monitor, a software-only screen (Virtual Display Driver, installed off by the `windows-agent-tools` role), run the game there, and unplug it after:

```bash
ssh $PC 'C:\tools\virtual-display.ps1 -On -TvOff'      # prints "virtual monitor: \\.\DISPLAY<n>"
ssh $PC 'C:\tools\run-on-desktop.ps1 -Path C:\bnbtest\windows\Game.exe -KeepDisplayOn'
ssh $PC 'C:\tools\screenshot.ps1 -Display <n>'
ssh $PC 'Stop-Process -Name Game; C:\tools\virtual-display.ps1 -Off'
```

- **`-TvOff` or not.** With `-TvOff` the virtual monitor is the only screen until `-Off`, so the game and the screenshot need no monitor number, and the TV gets no picture from the PC. Use it when the TV is on another source: `-KeepDisplayOn` wakes the displays, and a TV with automatic source switching can jump to the PC's input. Without `-TvOff` the virtual monitor sits beside the TV, which keeps showing what it was. Use that when someone is watching the PC on the TV; start the game with Unity's `-monitor <m>` and check with a screenshot which `m` lands on the virtual monitor. The game takes keyboard focus while it runs either way.
- **It is 3840x2160 at 300% by default**, the TV's mode. `-Scale` takes any Windows scale (100, 125, ... 500); the resolutions it offers are `windows_agent_tools_vdd_resolutions` in the role.
- **Always `-Off` when done.** An enabled virtual monitor is a screen nobody can see, and windows that open on it are lost to whoever is at the TV. If a test dies with the TV off, `-Off` brings it back; so does a reboot, because `-TvOff` is not saved.

## windows-media, retired 2026-11-20

The living-room PC `windows-media` (192.168.1.8) was the only Windows PC here. Windows is gone: on 2026-11-20 it was installed by PXE as `tuf-b550`, a native CachyOS k3s agent and Unity CI host with 4 slots, in place of the Hyper-V VM `windows-media-ci` (192.168.1.27) it ran. Its entries are in `ansible/inventory.yml`; how it joined is [runbooks/bare-metal-host.md](runbooks/bare-metal-host.md), how its slots run is [runbooks/unity-ci-host.md](runbooks/unity-ci-host.md), and its hardware is in [unity-ci-hardware.md](unity-ci-hardware.md). The opt-in org runner `windows-media` and its role `github-actions-runner-windows` went with it; both are in git history.
