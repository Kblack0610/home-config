# Fleet workers

Hosts that fleet-dispatch places agent sessions on, over ssh. The roster is the `fleet_workers` group in [`ansible/inventory.yml`](../../ansible/inventory.yml), and the dotfiles read it from that file alone (`.local/lib/fleet-hosts.sh`), so its vars stay there, not in `group_vars/`. The dispatch side, what it places and why: dotfiles-private `.config/agentctl/QUEUE.md`, "Fleet workers".

## What a worker takes

- A light claude queue item with no plan and no `#cloud-ok` tag, in a repo listed in the host's `fleet_repos`, when this workstation is over its load or idle pace. Heavy (Unity) items, plan items and other harnesses stay on the workstation.
- At most `fleet_max_live` sessions at once, and none while its own 1-minute load per core is over the dispatch pace (`DISPATCH_MAX_LOAD`).
- A session there runs with `AGENT_STATE_HOST=<fleet_state_host>` on its launch line, so its `agent-queue` and `agent-ask` calls run on the workstation over ssh. The queue and the asks stay in one store.

## Add a worker

1. Add the host under `fleet_workers` with `fleet_place: false`. Its `ansible_host` can come from another group.
2. From `ansible/`: `ansible-playbook playbooks/site.yml --limit <host> --tags fleet-worker`. Role `fleet-worker` installs tmux, stow, gh, codex, opencode and Claude Code, stows the dotfiles (a plain `~/.zshrc` or `~/.bashrc` is kept as `*.pre-dotfiles`), clones `fleet_repos`, creates the account dirs `claude-acct` knows, makes `~/.ssh/id_ed25519` and trusts the state host's host key.
3. The play's last task prints what is left, all of it the human's (no agent copies a token):
   - `gh auth login` on the worker, then re-run the play: that stows the private overlay and clones a private repo.
   - Add the worker's public key, which the task prints, to `~/.ssh/authorized_keys` on the state host.
   - Sign in each account: `claude`, then `/login`, once per account dir (`CLAUDE_CONFIG_DIR=<dir> claude` for each one but the main).
4. When the play prints `Ready`, set `fleet_place: true` for the host and merge. fleet-dispatch reads the inventory as this repo's checkout's upstream branch holds it, fetched on each pass, so no pull is needed. On the workstation, `fleet-dispatch hosts` shows the worker as `open`, or the reason it takes nothing.

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
     ansible_ssh_common_args: <the SSM ProxyCommand aws-burst-1 has>
     fleet_tailscale: true
     fleet_ssh_host: fleet-worker-1         # its tailnet name: fleet-dispatch ssh goes there
     fleet_state_host: cachyos-x8664-main   # the workstation's tailnet name
     fleet_power: governor
     fleet_place: false                     # no fleet_repos: the group's list (no Unity repo) is its own
   ```
4. Start it with the agent-web Infra page's "on" button and run the play. Its report starts with `sudo tailscale up --hostname fleet-worker-1`: run it in `aws ssm start-session --profile bnb --region us-east-2 --target <instance id>` and open the login link it prints, then do the rest of "Add a worker", step 3.
5. When the play prints `Ready`, set `fleet_place: true`.

## Check a worker

- `fleet-dispatch hosts` (or `--json`): each worker's free sessions, load, and why it takes nothing now.
- `fleet-sessions` lists a worker's sessions with their host; their targets read `<host>@lab/<session>:<window>`.
- A worker that does not answer ssh takes nothing, and reap leaves its items running until it answers again.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `fleet-dispatch hosts` says `<host> did not answer over ssh` | The host is down, or the workstation's key is not on it | `ssh <ansible_user>@<ansible_host> true` from the workstation |
| `<host> has no wt, no claude, or an agent-queue that does not forward` | The play has not run, the stow did not finish, or its dotfiles are older than the forwarding | Re-run the play (it pulls the dotfiles to main); check `~/.local/bin/wt`, `~/.local/bin/claude` and `~/.local/bin/agent-queue` on the host |
| The cloud worker never starts with items waiting | No demand key: fleet-dispatch has no `CC_KUBE_CONTEXT`, a global gate holds, home is under its gate, or no waiting item is in its `fleet_repos` | `ci_governor_power_demand{host="fleet-worker-1"}`; the dispatch log's demand line says what it wrote and why |
| The cloud worker stops with a session on it | fleet-dispatch stopped writing demand (the key ran out), or its `max_hours_day` | `systemctl --user status fleet-dispatch.timer` on the workstation; the governor log's stop line names the cause |
| A session's `agent-queue` call prints `the state host ... did not answer over ssh` | The worker's key is not in the state host's `authorized_keys`, or its host key changed | Step 3 of "Add a worker"; re-run the play for the host key |
| `fleet-dispatch hosts` says `no fleet workers`, or shows an old `fleet_place` | The workstation's dotfiles predate the upstream read, or its fetch of this repo fails | `git -C ~/dev/home/home-config fetch` must succeed; `fleet-dispatch hosts` fetches before it reads |
| The play stops at `Clone the public dotfiles` with `Local modifications exist` | An older role cloned the dotfiles submodules and one stopped half checked out | On the worker: `git -C ~/.dotfiles submodule deinit -f <path>`, then re-run the play |
