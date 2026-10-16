# Windows Machines

How a Windows PC joins the fleet. The goal is one short visit to the PC, then everything else from the workstation over SSH.

What "on the fleet" means today: the PC pushes a liveness heartbeat every 60s to `fleet.kblab.me` and shows up on the fleet dashboard, Grafana and the status-bar glyph. It is not Ansible-managed yet. The mechanism is shared with every other computer and is described in [device-fabric.md](./device-fabric.md) and `apps/gatus-fleet/README.md`.

## Inventory

| Name (fleet key) | Windows hostname | IP | User | Notes |
|------------------|------------------|----|------|-------|
| `living-room-pc` (`homelab_living-room-pc`) | `DESKTOP-BPLB04U` | 192.168.1.121 (`living-room-pc-lan`) | `hughl` (Microsoft account, admin) | Wi-Fi only (Intel AX210), enrolled 2026-10-16 |

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

Add a reservation to `infrastructure/dhcp/devices.yaml` (desktops go in `.30-.49`, living-room/media boxes in `.120-.139`), then `dhcp diff` and `dhcp --force sync` per `infrastructure/dhcp/README.md`. Use the MAC of the adapter that is actually `Up` in `Get-NetAdapter -Physical`; a PC on Wi-Fi has a different MAC from its unplugged ethernet port. The PC keeps its old address until its lease renews, so reboot it once from SSH: `shutdown /r /t 60 /c "..."` gives anyone at the screen a minute to cancel with `shutdown /a`.

### 4. Enroll over SSH (workstation)

`enroll.ps1` fetches the pusher, sends one probe heartbeat, and only registers the Scheduled Task if the probe got through. Feed it the token on stdin so it never lands on a command line or in shell history. Use `printf`, not `echo`: zsh's `echo` turns `\e` in a Windows path into an escape character.

```bash
PC=hughl@192.168.1.121 NAME=living-room-pc
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
curl -s https://fleet.kblab.me/api/v1/endpoints/statuses | jq -r '.[] | select(.key=="homelab_living-room-pc") | .results[-1] | "\(.timestamp) \(.success)"'
```

A timestamp under a minute old and `true`. Check again a few minutes after the S4U switch: the task's `LastTaskResult` is 0 even when a push fails (the pusher exits 0 by contract), so only fresh results in gatus prove the task is pushing.

## Gotchas

- **The PIN is not a password.** A Windows Hello PIN only unlocks the PC at the PC. SSH, Remote Desktop and file sharing want the account password, which on a Microsoft-account login is the Microsoft account password. Step 2 uses keys, so nobody needs it.
- **Admins ignore `~/.ssh/authorized_keys`.** For a user in Administrators, Windows OpenSSH reads only `C:\ProgramData\ssh\administrators_authorized_keys`, and only if its ACL is Administrators + SYSTEM. Step 2 writes both files so it works either way.
- **"No route to host" means the network card is asleep, not that the PC is off.** Nothing answered on the LAN at all. On 2026-10-16 the living-room PC was on with the screen awake and still dropped off the network between checks. It is on Wi-Fi with Windows' default power saving, and the router had also logged a `169.254.x` fallback address for its MAC. Step 2 turns the Wi-Fi and network-card power saving off for this reason.
- **`powershell -Command -` reads stdin line by line.** One-line statements work; a multi-line `foreach { ... }` block silently produces nothing. Send anything multi-line as `-EncodedCommand $(iconv -t UTF-16LE script.ps1 | base64 -w0)`.
- **`Set-NetAdapterPowerManagement` has no `-AllowComputerToTurnOffDevice` parameter**, and `Disable-NetAdapterPowerManagement` leaves that checkbox on. The registry `PnPCapabilities = 24` in step 2 is what clears it.
- **Interactive vs S4U.** `enroll.ps1` registers the task as Interactive so it works without admin on a locked-down work machine. That flashes a console every minute and stops at logoff. On a home PC that is admin-controlled, S4U is the right setting (step 4).
- **A wrong name or group is a silent 404.** The gatus key is `<group>_<name>`. `enroll.ps1` probes first and stops on a 404, so a machine is never left installed but reporting nothing.

## Not covered yet

Ansible management of Windows (packages, config). The SSH server from step 2 is the prerequisite for it; nothing in `ansible/` targets Windows today.
