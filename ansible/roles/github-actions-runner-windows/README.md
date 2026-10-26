# github-actions-runner-windows

An org-scoped GitHub Actions runner for testing Windows-specific issues on a real Windows PC (today `windows-media`, the living-room box). It is opt-in: it carries the runner's own `self-hosted`, `Windows`, `X64` labels plus `host-<inventory_hostname>`, and no CI workflow asks for those, so a job lands here only when it says `runs-on: [self-hosted, windows]`. It is not a merge gate and not part of the Unity pool.

It runs in the logged-on user's desktop session, started at logon by the scheduled task `actions-runner`, not as a Windows service. A service lives in session 0, where a game build gets no window on the screen and no display GPU, which defeats the point of testing on this box. So the runner is offline whenever nobody is logged on at the PC. The task runs at Task Scheduler priority 7 (below normal for CPU, I/O and memory), and every job step inherits it, so what is on the TV wins.

Registration tokens are minted on the control node with `gh` (needs `admin:org`), like the Unity pool. There is no PAT path: the in-cluster ansible-runner does not manage Windows hosts.

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit windows-media --tags runner --check --diff
ansible-playbook playbooks/site.yml --limit windows-media --tags runner
gh api orgs/BlackNBrownStudios/actions/runners --jq '.runners[] | select(.name=="windows-media") | "\(.status) \(.busy) \([.labels[].name] | join(","))"'
```

Remove it with `-e gh_runner_win_state=absent`: that stops and deletes the task, deregisters the runner and deletes `C:\actions-runner`.

Driving the PC directly (SSH, launching a build on the TV, screenshots) is the `windows-agent-tools` role; see `docs/windows-machines.md`.
