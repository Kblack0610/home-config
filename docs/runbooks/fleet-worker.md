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
4. When the play prints `Ready`, set `fleet_place: true` for the host and merge. On the workstation, `fleet-dispatch hosts` shows the worker as `open`, or the reason it takes nothing.

## Check a worker

- `fleet-dispatch hosts` (or `--json`): each worker's free sessions, load, and why it takes nothing now.
- `fleet-sessions` lists a worker's sessions with their host; their targets read `<host>@lab/<session>:<window>`.
- A worker that does not answer ssh takes nothing, and reap leaves its items running until it answers again.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `fleet-dispatch hosts` says `<host> did not answer over ssh` | The host is down, or the workstation's key is not on it | `ssh <ansible_user>@<ansible_host> true` from the workstation |
| `<host> has no wt, no claude, or an agent-queue that does not forward` | The play has not run, the stow did not finish, or its dotfiles are older than the forwarding | Re-run the play (it pulls the dotfiles to main); check `~/.local/bin/wt`, `~/.local/bin/claude` and `~/.local/bin/agent-queue` on the host |
| A session's `agent-queue` call prints `the state host ... did not answer over ssh` | The worker's key is not in the state host's `authorized_keys`, or its host key changed | Step 3 of "Add a worker"; re-run the play for the host key |
