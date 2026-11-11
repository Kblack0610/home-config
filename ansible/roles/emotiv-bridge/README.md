# emotiv-bridge

Records the Emotiv EPOC X on mac-mini whenever it is connected in the EMOTIV Launcher, and archives every stream encrypted to the NAS. Plan: `~/.claude/plans/emotiv-stream.md` (P2). The NAS end is `roles/emotiv-archive-target`.

## What it does

One Node process (`files/emotiv-bridge.mjs`, no packages beyond Node), run by the LaunchAgent `com.kblab.emotiv-bridge`:

1. Connects to Cortex at `wss://localhost:6868`, trusting the Emotiv CA exported from the System keychain.
2. When a headset is `connected`, authorizes with `debit: 1` and opens one active session per wear, then subscribes each stream in `emotiv_bridge_streams`. Raw `eeg` is refused under the Basic license (-32016) and logged; everything else records.
3. Writes each stream to `<spool>/<YYYY-MM-DD>/<start>-<session>/<stream>-<NNNN>.jsonl.gz`. Line 1 is a header (`stream`, `cols`, `session`, `headset`, `chunk`); every other line is `{"time": <epoch s>, "<stream>": [...]}` as Cortex sent it.
4. Every 5 minutes, and when the headset disconnects, closes the chunks, encrypts each with `age` to the EEG recipient and deletes the plaintext.
5. Uploads the `.age` files with rsync over ssh to `emotiv-archive@asus-laptop`, where rrsync confines the key to writing into `/mnt/nas/private/eeg`. rsync deletes a spool file only after it has been transferred and checked. A failed upload retries with backoff (30 s doubling to 15 min) and the spool keeps everything.
6. Publishes a retained heartbeat every 60 s to `emotiv/mini/bridge` on the LAN broker: `up`, `wearing`, `session_seconds`, `spool_bytes`, `last_upload_epoch`, `last_error_code`, `local_quota`, `license_valid_to_epoch`. Alerts on it are P3.

Cortex pushes warnings for connect (104), disconnect (102, 103), session closed (1) and streams stopped (0), and the bridge acts on them. Cortex sends nothing when a headset is switched on, so the bridge asks for a scan every `emotiv_bridge_scan_interval_seconds`.

If `createSession` fails (EmotivPRO holding the headset, for one), the bridge does not try again until the headset disconnects, so it spends one debit per wear at most and gives way to EmotivPRO.

## Keys

| What | Where | Who can read it |
|---|---|---|
| EEG identity (decrypts the archive) | `eeg-identity.sops.yaml` here, keys `identity` and `recipient` | The main SOPS key only (`.sops.yaml` first rule). Never on the mini, not readable by the ansible-runner |
| EEG recipient (encrypts) | `emotiv_bridge_age_recipient` in `defaults/main.yml` | Public |
| Cortex app Home-EEG client id and secret | `vault_emotiv_cortex_client_*` in `group_vars/macos_hosts/vault.sops.yaml`; the human's copy is rbw `emotiv-cortex` | Rendered to `~/.config/emotiv-bridge/cortex.json` (0600) |
| Upload ssh key | `vault_emotiv_archive_ssh_key` (same vault); public half in `roles/emotiv-archive-target` | Rendered to `~/.config/emotiv-bridge/archive_ed25519` (0600) |

Losing the EEG identity makes the archive unreadable forever. It is backed up wherever this repo and the SOPS main key are.

## Apply

The NAS end first, then the bridge:

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit asus-laptop --tags emotiv
ansible-playbook playbooks/site.yml --limit asus-laptop --tags backup-mounts   # @eeg subvolumes for the eeg-backup mirror
ansible-playbook playbooks/site.yml --limit mac-mini --tags emotiv
```

## Read a session (workstation)

```bash
ssh -p 2222 192.168.1.152 'sudo tar -C /mnt/nas/private/eeg -cf - 2026-11-11' | tar -xf - -C ~/eeg
age -d -i <(sops decrypt --extract '["identity"]' ansible/roles/emotiv-bridge/eeg-identity.sops.yaml) \
  ~/eeg/2026-11-11/<session>/mot-0001.jsonl.gz.age | zcat | head
```

## Check it

```bash
ssh mac-mini 'launchctl print gui/$(id -u)/com.kblab.emotiv-bridge | grep -E "state|pid"; tail -20 ~/Library/Logs/emotiv-bridge.log'
ssh mac-mini 'find ~/Library/Application\ Support/emotiv-bridge/spool -type f | head'   # empty unless wearing or the NAS is unreachable
mosquitto_sub -h mosquitto.kblab.me -p 31883 -t emotiv/mini/bridge -C 1
```

Tests: `node --test ansible/roles/emotiv-bridge/files/test-emotiv-bridge.mjs` (CI job `ci-disk-prune`).

## Rollback

`launchctl bootout gui/$(id -u)/com.kblab.emotiv-bridge` on the mini and remove the role from the mac-mini play. The archive, the keys and the NAS user stay.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Log shows `authorize failed` with -32021 | Wrong client secret: compare the vault with rbw `emotiv-cortex`, re-apply |
| Log shows `createSession failed` while wearing | EmotivPRO or another app holds the headset. The bridge waits for the next disconnect. Close the other app's session and switch the headset off and on |
| `spool_bytes` grows while not wearing | Uploads failing: the log names the rsync exit code. 255 is ssh (asus down, host key changed: update `emotiv_bridge_upload_host_key`) |
| LaunchAgent restarts every 30 s, `last exit code = 0`, empty log | The script was started through a symlinked path and skipped main(); fixed by the realpath guard. The bridge home stays under `~/Library/Application Support`, never `~/.local`, which on the mini is a symlink into the dotfiles repo |
