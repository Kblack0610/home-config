# Fleet workers

Hosts that fleet-dispatch places agent sessions on, over ssh. The roster is the `fleet_workers` group in [`ansible/inventory.yml`](../../ansible/inventory.yml), and the dotfiles read it from that file alone (`.local/lib/fleet-hosts.sh`), so its vars stay there, not in `group_vars/`. The dispatch side, what it places and why: dotfiles-private `.config/agentctl/QUEUE.md`, "Fleet workers".

A machine the human drives the fleet from, but that runs no session, goes in `fleet_controllers` instead (today `cachyos-rog`, the ROG Flow). Only the host-identity play targets that group (host keys, see "Host keys and NIC reservations"), and `fleet-hosts.sh` never places work there. Never move a controller into `fleet_workers` to give it a session.

## What a worker takes

- A light claude queue item with no plan and no `#cloud-ok` tag, in a repo listed in the host's `fleet_repos`, when the fleet home is over its load or idle pace. Heavy (Unity) items, plan items and other harnesses stay on the home.
- At most `fleet_max_live` sessions at once, and none while its own 1-minute load per core is over the dispatch pace (`DISPATCH_MAX_LOAD`).
- A session there runs with `AGENT_STATE_HOST=<fleet_state_host>` on its launch line, so its `agent-queue` and `agent-ask` calls run on the fleet home over ssh. The queue and the asks stay in one store.

## Add a worker

1. Add the host under `fleet_workers` with `fleet_place: false`, and its `ssh_host_ed25519` (its `/etc/ssh/ssh_host_ed25519_key.pub`, read over ssh you already trust). Its `ansible_host` can come from another group.
2. From `ansible/`: `ansible-playbook playbooks/site.yml --limit <host> --tags fleet-worker`. Role `fleet-worker` installs tmux, stow, gh, codex, opencode, a Rust toolchain and Claude Code, stows the dotfiles (a plain `~/.zshrc` or `~/.bashrc` is kept as `*.pre-dotfiles`), clones `fleet_repos`, clones home-config to `~/dev/home/home-config` (anonymous HTTPS, remote `forgejo`) with `fleet-roster-fetch.timer` fetching it every 10 minutes, so the worker reads the roster too (without it `fleet-is-home` passes on every worker, since no roster names a home), creates the account dirs `claude-acct` knows, builds `agent-panel` from the dotfiles (`cargo build --release`, as the user, on Arch only) and links it to `~/.local/bin/agent-panel` for the workstation's `fleet-sessions`, makes `~/.ssh/id_ed25519`, and writes every roster machine's host key into `/etc/ssh/ssh_known_hosts` (see "Host keys" below), enables lingering so its user timers run with nobody logged in, and joins the agent store as a peer (see "The agent store").
3. The play's last task prints what is left, all of it the human's (no agent copies a token):
   - `gh auth login` on the worker, then re-run the play: that stows the private overlay and clones a private repo.
   - Add the worker's public key, which the task prints, to `~/.ssh/authorized_keys` on the state host.
   - Add its store key, which the task prints, as a write deploy key on Forgejo `kblack0610/.agent`, then re-run the play: it joins the store (see "The agent store").
   - Sign in each account: `claude`, then `/login`, once per account dir (`CLAUDE_CONFIG_DIR=<dir> claude` for each one but the main).
4. When the play prints `Ready`, set `fleet_place: true` for the host and merge. fleet-dispatch reads the inventory as this repo's checkout's upstream branch holds it, fetched on each pass, so no pull is needed. On the workstation, `fleet-dispatch hosts` shows the worker as `open`, or the reason it takes nothing.

## The fleet home

The machine named by the group's `fleet_state_host` is the fleet home: the workstation, `pc-home-cachy-main` (`razer-laptop` held it 2026-11-15 to 11-16 while the workstation was down). Other hosts forward their queue and ask writes to it, it serves agent-web (`apps/agents`), and the singleton lanes (dispatch, sentinel, fleet-state) run only there. A worker that is the home keeps `fleet_place: false`: sessions run on it as the home, not as placed work. A machine knows it is the home when `fleet_state_host`, up to its first dot, is its hostname, or the whole name is its `ansible_host`, or its `fleet_ssh_host` (the workstation's entry gives `pc-home-cachy-main.lan`, its DHCP name), with no DNS lookup; a home named any other way stands down.

When the home is a fleet worker, the same play sets it up; the workstation is set up by its own play and dotfiles. On a worker home the role skips the ssh to the state host (it is the state host) and runs `tasks/home.yml`, which:
- builds `agent-web` and links it to `~/.local/bin/agent-web`;
- builds `hub` (the hub-tui crate) and links it to `~/.local/bin/hub`, which `fleet-state.service` runs after each tick (`hub needs --page`). Arch only, like agent-web, and re-run the play after a dotfiles change to hub-core or `channels.toml` to rebuild it. Without it the unit logs "Unable to locate executable hub" every minute (razer-laptop, 2026-11-15). The first tick with the binary pages once for the NEEDS YOU rows already open, at most 3 named, under the score bar and daily cap in `channels.toml` `[pager.needs]`;
- builds `notes` with `notes-bootstrap --build-only` (the private overlay's installer: it builds the notes-cli crate, publishes it to `~/.local/lib/notes` and links `~/.local/bin/notes`), which `agent-queue` runs to file a board row on add and close it on done. Arch only, and skipped until the overlay is stowed; it also links `notes-sync`, `notes-watch` and `~/.config/notes`, and exits 0 when its build fails, so the play reads `~/.local/bin/notes` back and the report names a missing one. Without it `agent-queue add` says "no story row filed: no notes on PATH" and `agent-queue done` says "notes: command not found", and no item files or closes its row (razer-laptop, 2026-11-15 to 11-16). The binary alone files nothing: the rows live in the `~/.notes` store, which the play does not clone, so a home with no `~/.notes` still files no rows;
- writes `~/.config/schedules-sync/kubeconfig` for the `monitoring/schedules-sync` account, which may only read the github-runner-exporter feed (`apps/github-runner-exporter/feed-reader-rbac.yaml`), and a `schedules-sync.service` drop-in that sets `KUBECONFIG` to it. Without it the feed never answers, no `~/.local/state/schedules/state.json` is written, and every `nightly:` queue hold waits on "no schedules state". `~/.kube/config` is not touched;
- starts `agent-web.service` once `~/.config/agent-web/oauth.json` exists.

Left for the human on the home, printed by the play besides what "Add a worker" step 3 lists: `oauth.json` from rbw item `agent-web-oauth-client` (`{"client_id": ..., "client_secret": ...}`, mode 0600), the sign-ins, then `agentctl reload` on the home to arm the lanes.

Moving the home: on the old home run `store-sync` once and check its last line in `~/.agent/.sync/sync.log` is COMPLETE, so origin holds every queue and ask write, then stop its lanes. Change `fleet_state_host` and the Endpoints IP in `apps/agents/service.yaml` in one PR and run the play on the new host. Both are already store peers with their own deploy keys, so no key moves and the old home's stays.

### Rotate the feed token

The old token stops working at step 1, so the home's passes fail on the feed until step 3; it catches up from its cursor afterwards.

1. `kubectl -n monitoring delete secret schedules-sync-token`, `flux reconcile kustomization apps`, then `kubectl -n monitoring wait --for=jsonpath='{.data.token}' secret/schedules-sync-token`: the token controller fills the new Secret.
2. Store its `token` and `ca.crt` (both base64-decoded) as `vault_fleet_home_feed_token` and `vault_fleet_home_feed_ca`: `sops ansible/group_vars/fleet_workers/vault.sops.yaml`. Check it first: `kubectl --kubeconfig=/dev/null --server=<k3s_url> --certificate-authority=<ca file> --token=<token> get --raw '/api/v1/namespaces/monitoring/services/github-runner-exporter:9320/proxy/api/ci/details?cursor=0&limit=1'` prints the feed.
3. Re-run the play on the home (`--tags fleet-worker -l <home>`), then `systemctl --user start schedules-sync` there and check `~/.local/state/schedules/state.json` moved.

## The agent store

Every worker holds a peer clone of the agent store (`~/.agent`, the queue, the asks and the logs), which `store-sync` commits, merges and pushes: each write at once, and `store-sync.timer` every 15 minutes. The design, the remotes and the log lines: dotfiles-private `.config/agentctl/QUEUE.md`, "The store: every machine a peer". The play (`tasks/store.yml`) runs its join steps:

1. Makes `~/.ssh/id_agent_sync`, trusts Forgejo's pinned host key (`fleet_store_host_key` in the role defaults, never a scan) and checks the key reaches `STORE_PRIMARY_URL` from `~/.config/agentctl/store.env`, the private overlay's file that `store-sync` reads too, so the play holds no store URL of its own. Until the key is a write deploy key on Forgejo `kblack0610/.agent`, the report prints it with the error.
2. Once it reaches, stops the lanes and `agent-web` and runs `store-sync join`, once (it creates `~/.agent/.sync/peer`). A clean `~/.agent` not ahead of origin joins in place; any other becomes a fresh clone, the old dir is kept at `~/.agent.pre-peer-<stamp>`, and that run's report names it. Join a machine with no live session: one still running writes into the kept copy.
3. Enables `store-sync.timer`, which the dotfiles stow.

## Cloud worker

`fleet-worker-1` is an Ubuntu Spot box in AWS (platform `infra/aws-ci-burst`, its `hosts` map) with no CI runners. The governor powers it: fleet-dispatch writes the key `fleet-worker-1.demand` into the `ci-runner-governor-override` ConfigMap on each pass (its live sessions plus the items waiting while every home host is at its gate), the governor starts it on demand and stops it after `idle_off_seconds` without any (`apps/ci-runner-governor/governor.py`, the "workers" paragraph). It takes only items in a repo in its `fleet_repos`, so leave every Unity repo out. A stop keeps its root disk and the sign-ins on it; a Terraform replace drops them.

1. Apply the platform module (it costs money, so the human runs it) and take the instance id from `terraform output -json instance_ids`.
2. Add the governor entry to `apps/ci-runner-governor/config.json`, `workers`. Keep `idle_off_seconds` at 1800 or more: fleet-dispatch writes demand every 15 minutes, with a 40-minute end, and only for a worker with `fleet_place: true`, so the entry starts nothing before step 5.
   ```json
   {"host": "fleet-worker-1", "power": {"provider": "aws", "instance_id": "<instance id>", "region": "us-east-2",
    "idle_off_seconds": 1800, "start_min_excess": 1, "max_hours_day": 12, "fallback_seconds": 3600,
    "live": true}}
   ```
   Its price goes in `rates`, the one home of every host's $/h: `"fleet-worker-1": {"usd_per_hour": 0.076, "basis": "spot-estimate"}`.
3. Add it under `fleet_workers`:
   ```yaml
   fleet-worker-1:
     ansible_host: <instance id>
     ansible_user: ubuntu
     ansible_ssh_common_args: <the SSM ProxyCommand aws-spot-1 has>
     fleet_tailscale: true
     fleet_ssh_host: fleet-worker-1         # its tailnet name: fleet-dispatch ssh goes there
     fleet_state_host: cachyos-x8664-main   # the workstation's tailnet name
     fleet_power: governor
     fleet_place: false                     # no fleet_repos: the group's list (no Unity repo) is its own
   ```
4. Start it with the agent-web Infra page's "on" button and run the play. It joins the tailnet with the auth key from SOPS (role `tailscale-node`); then do the rest of "Add a worker", step 3.
5. When the play prints `Ready`, set `fleet_place: true`.

## Check a worker

- `fleet-dispatch hosts` (or `--json`): each worker's free sessions, load, and why it takes nothing now.
- `fleet-sessions` lists a worker's sessions with their host; their targets read `<host>@lab/<session>:<window>`. It runs `agent-panel list --json` on the worker, so `fleet_host_run <host> agent-panel list --json` must exit 0 or 3 (3 is no sessions).
- A worker that does not answer ssh takes nothing, and reap leaves its items running until it answers again.

## Host keys and NIC reservations

Role `host-identity` (the last play in `site.yml`, tag `host-identity`; `fleet-worker` imports its known_hosts half) writes one block into `/etc/ssh/ssh_known_hosts` on each machine in `desktop_workstations`, `fleet_workers` and `fleet_controllers`. The block holds every one of those machines' `ssh_host_ed25519` from the inventory, under each name it may be dialed by: its inventory name, `ansible_host`, `fleet_ssh_host`, its `devices.yaml` name and IP, and both names with `.lan`. So a machine that is off when the play runs is still trusted, and a home move to any of them needs no new trust. Keys are never scanned off the network. The file is system-wide, so no user's `~/.ssh` is touched, and ssh accepts a key there even when a stale one sits in `~/.ssh/known_hosts`.

The same play fails a host whose live host key is not its inventory `ssh_host_ed25519` (a rebuild), or whose NICs carry none of the MAC its `infrastructure/dhcp/devices.yaml` entry reserves (a new motherboard or dongle). The message names the line to change. The nightly `ansible-runner` check runs it on the LAN servers. Apply: `ansible-playbook playbooks/site.yml --limit <host> --tags host-identity -K`, then the same for every other roster machine after a key changes.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `fleet-dispatch hosts` says `<host> did not answer over ssh` | The host is down, or the workstation's key is not on it | `ssh <ansible_user>@<ansible_host> true` from the workstation |
| `<host> has no wt, no claude, or an agent-queue that does not forward` | The play has not run, the stow did not finish, or its dotfiles are older than the forwarding | Re-run the play (it pulls the dotfiles to main); check `~/.local/bin/wt`, `~/.local/bin/claude` and `~/.local/bin/agent-queue` on the host |
| The cloud worker never starts with items waiting | No demand key: fleet-dispatch has no `CC_KUBE_CONTEXT`, a global gate holds, home is under its gate, or no waiting item is in its `fleet_repos` | `ci_governor_power_demand{host="fleet-worker-1"}`; the dispatch log's demand line says what it wrote and why |
| The cloud worker stops with a session on it | fleet-dispatch stopped writing demand (the key ran out), or its `max_hours_day` | `systemctl --user status fleet-dispatch.timer` on the workstation; the governor log's stop line names the cause |
| A session's `agent-queue` call prints `the state host ... did not answer over ssh` | The worker's key is not in the state host's `authorized_keys`, or the state host's key is not in the worker's `/etc/ssh/ssh_known_hosts` (`Host key verification failed`, cachyos-rog to `pc-home-cachy-main.lan` 2026-11-17) | Step 3 of "Add a worker"; for the host key, "Host keys and NIC reservations" |
| The host-identity play fails `<host>'s ssh host key is not the inventory's` | The host was rebuilt and sshd made a new key | Set the `ssh_host_ed25519` it prints after checking it on the host's console or over ssh you already trust, merge, then re-run `--tags host-identity` on every roster machine |
| The host-identity play fails `devices.yaml reserves ... which no NIC on <host> has` | Its NIC changed (motherboard swap, new dongle), so the next lease renewal moves it off its IP | Set the `mac` it lists in `infrastructure/dhcp/devices.yaml`, merge, then `dhcp sync` |
| `fleet-dispatch hosts` says `no fleet workers`, or shows an old `fleet_place` | The workstation's dotfiles predate the upstream read, or its fetch of this repo fails | `git -C ~/dev/home/home-config fetch` must succeed; `fleet-dispatch hosts` fetches before it reads |
| `fleet-is-home` exits 0 on a worker that is not the home, or `fleet-dispatch hosts` on a worker home lists nobody | The worker has no `~/dev/home/home-config`, or its fetch fails, so it reads no roster | Re-run the play; on the worker `git -C ~/dev/home/home-config fetch forgejo master` must succeed and `fleet-is-home` must name the inventory's home |
| `fleet-sessions` lists the worker as silent, `agent-panel` exits 127 | The play has not run since the role learned to build it, or the build failed | Re-run the play; `cargo build --release` in `~/.dotfiles/.local/src/agent-panel` on the worker shows the error |
| `Clone the public dotfiles` or a later pull fails with `Local modifications exist` or an SSH submodule fetch error | A copied `~/.dotfiles` has local edits or initialized submodules with stale SSH URLs | On the worker, inspect `git -C ~/.dotfiles status` and `git -C ~/.dotfiles submodule foreach git status`; preserve any submodule work before deinitializing. Stash parent-repo edits with `git -C ~/.dotfiles stash push -u`, then run `git -C ~/.dotfiles submodule deinit -f --all` and re-run the play. The role sets repo-local `fetch.recurseSubmodules=false` for later pulls; review the saved stash before restoring edits |
| The report says `the store did not take the sync key (...)` | The key is not a write deploy key on `kblack0610/.agent`, or Forgejo's ssh (`forge-primary.kblab.me:2222`, over the tailnet) does not answer from the worker | Add the printed key with write access (`forgejo-ops`); `ssh -i ~/.ssh/id_agent_sync -p 2222 git@forge-primary.kblab.me` on the worker answers with a greeting once it works |
| The report says `store-sync join failed (...)` | Origin went away between the play's check and the join, or the clone or the move of the old dir failed (a `~/.agent.peer-new-*` or `~/.agent.pre-peer-*` in the way) | On the worker, `store-sync join` by hand shows the whole error, and `~/.agent/.sync/sync.log` has each JOIN and ERROR; fix it, re-run the play, then `agentctl reload`, since the join stopped the lanes |
| The report says `store-sync join kept the old store at ~/.agent.pre-peer-<stamp>` | `~/.agent` was not a clone, was dirty, or held commits origin lacked, so the join took a fresh clone | `git -C ~/.agent.pre-peer-<stamp> log origin/master..` and `status` show what origin lacks; copy any queue or ask file still wanted into `~/.agent`, let `store-sync` push it, then delete the old dir |
| The report says `the store did not take the sync key (Host key verification failed.)` | Forgejo's ssh host key changed, or store.env names a host other than `fleet_store_host` | Read the pod's `/data/ssh` ed25519 public key (apps/forgejo) and update `fleet_store_host_key` in the role defaults, or bring `fleet_store_host` and store.env back to the same host; never trust a scanned key |
