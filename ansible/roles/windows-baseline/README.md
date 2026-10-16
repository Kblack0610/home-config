# windows-baseline

Lean, gaming-first baseline for a Windows PC reached over Windows OpenSSH. Written for `living-room-pc` (Win10 Pro, a TV-connected gaming and playtest box), and kept generic: every app, service, task and registry value it touches is a list in `defaults/main.yml`, so another Windows host overrides data, not tasks.

## What it does

1. Removes consumer and OEM Store apps for all users and de-provisions them so new profiles and feature updates do not bring them back. Xbox identity, Game Bar, Store, winget, Photos, Calculator and the codec extensions are kept on purpose: Xbox-account games need the identity provider.
2. Uninstalls the ASUS MSI products (ROG Live, AURA, AIOFan HAL, Update Helper) and disables the services and scheduled tasks of the ones with no silent uninstaller (Armoury Crate Service, the ASUS framework/cert/fan/update services). `DisableWpbtExecution=1` stops the firmware's WPBT table from reinstalling Armoury Crate, which would otherwise need a BIOS toggle. Fans and pump fall back to the BIOS curves and RGB to the board default.
3. Turns down telemetry, feedback, ads, suggested apps, Bing/Cortana/Copilot in Start, News and Interests, and Delivery Optimization uploads. Defender is left alone: on an out-of-support Win10 it is the protection that still updates.
4. Removes OneDrive: points Desktop, Documents and Pictures back at the local profile, copies only the files physically on disk (cloud-only placeholders stay in the OneDrive account, untouched), uninstalls the sync client and blocks it by policy. The old `%USERPROFILE%\OneDrive` folder is left in place.
5. Removes autostart nags (Logi Download Assistant, the OneDrive setup stubs).
6. Performance: High performance plan, no sleep on AC, Wi-Fi at max performance, network cards never powered off (`PnPCapabilities=24`), hibernation off (also disables Fast Startup), Game Mode on, hardware-accelerated GPU scheduling on, background Game DVR capture off.
7. Cleanup: temp files older than a week, the Windows Update download cache, superseded component versions. Reported as unchanged, so a converged run reads `changed=0`.

## Apply

```bash
cd ansible && ansible-galaxy collection install -r requirements.yml -p ./collections
ansible-playbook playbooks/site.yml --limit living-room-pc --check --diff
ansible-playbook playbooks/site.yml --limit living-room-pc
ansible living-room-pc -m ansible.windows.win_reboot -a "pre_reboot_delay=60"   # first apply only
```

GPU scheduling and the network-card power setting take effect after a reboot. `win_reboot` waits for the host to answer again instead of sleeping.

## Verify

A second run reports `changed=0`. Measured on living-room-pc, 2026-10-16, before -> after: C: free 29.9 -> 60.1 GB, Store apps 50 -> 20, provisioned 47 -> 21, running services 93 -> 80, processes 119 -> 104, ASUS services running 0, Balanced -> High performance.

## Gotchas

- `HibernateEnabled` is often absent; Windows then uses `HibernateEnabledDefault`. Reading only the first reports hibernation as off while a 27 GB `hiberfil.sys` sits on disk.
- Some Store apps only go once the apps that depend on them are removed, so the very first apply can need a second pass to settle.
- `win_powershell` cannot dry-run a script, so `--check` reports those tasks as changed without running them.
