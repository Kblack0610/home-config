# Ansible Runner

Flux-managed in-cluster runner for Ansible playbooks. Replaces Semaphore UI with a pure-declarative CronJob + Job flow. No UI to click through on first deploy.

## What runs, when

| Job | Schedule | Behavior |
|-----|----------|----------|
| `convergence-check` CronJob | 04:00 daily, America/Los_Angeles | Clones `home-config` at master, runs `ansible-playbook playbooks/site.yml --check --diff`, limited to the hosts in `HOSTS` (in `cronjob.yaml`) that answer SSH. Logs drift and writes the PLAY RECAP as metrics for the `ConvergenceDrift` alert. Does NOT change state. |

The playbook comes straight from git master on every execution — there is no baked-in version. Edit `ansible/` in git, push, next scheduled run picks it up.

## Manual runs

### Dry-run now

```bash
kubectl -n ansible-runner create job \
  --from=cronjob/convergence-check \
  convergence-manual-$(date +%s)

kubectl -n ansible-runner get jobs -w
kubectl -n ansible-runner logs -l job-name=convergence-manual-<id> --tail=200
```

### Apply (no --check)

Dry-runs catch drift. To actually apply changes, either:

**Option A — one-off Job from the CronJob, strip `--check`:**

```bash
kubectl -n ansible-runner get cronjob convergence-check -o yaml \
  | sed 's/--check//; s/--diff//' \
  | yq '.spec.jobTemplate.spec' - \
  | kubectl create -n ansible-runner -f - --dry-run=client -o yaml \
  | sed "s/name: convergence-check$/name: apply-$(date +%s)/" \
  | kubectl apply -f -
```

**Option B — SSH to workstation and run `ansible-playbook` directly:**

```bash
cd ~/dev/home/home-config/ansible
ansible-playbook playbooks/site.yml --limit hp-victus
```

(The CronJob is convenient for scheduled drift checks; Option B is fine for ad-hoc applies.)

## Secrets

`apps/ansible-runner/secret.yaml` is SOPS-encrypted and holds:

| Key | Purpose |
|-----|---------|
| `ssh-privkey` | Dedicated ed25519 keypair for the runner. Public key must be in `~/.ssh/authorized_keys` on every host in `HOSTS` (`cronjob.yaml`), and on pi5-master, where `roles/game-server-node` delegates `kubectl label`. Missing there, every run ended `UNREACHABLE` (fixed 2026-10-26). |
| `sops-age-key` | An age private key that decrypts `ansible/group_vars/*/vault.sops.yaml` and nothing else. Those files are encrypted to it and to the main key (`.sops.yaml`, first rule). An init container copies a pinned `sops` binary into `/workspace/bin`, since the semaphore image has none. |

### Rotating the SSH key

```bash
# 1. Generate new keypair on workstation
ssh-keygen -t ed25519 -N '' -C 'ansible-runner@home-k3s' -f /tmp/newkey

# 2. Install pub key on every target host (repeat per host in HOSTS)
cat /tmp/newkey.pub | ssh kblack0610@192.168.1.243 'cat >> ~/.ssh/authorized_keys && sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys'

# 3. Update SOPS secret with new privkey
sops apps/ansible-runner/secret.yaml     # paste /tmp/newkey contents into ssh-privkey

# 4. Commit + reconcile
git add apps/ansible-runner/secret.yaml
git commit -m "chore(ansible-runner): rotate SSH key"
git push
flux --context home-k3s reconcile kustomization apps --with-source

# 5. Remove old pubkey from each target's authorized_keys.
```

No pod restart dance: every CronJob execution gets a fresh pod that reads the current Secret.

### Rotating the Ansible age key

```bash
# 1. New key; the public half goes into .sops.yaml's ansible rule in place of the old one
age-keygen -o /tmp/ansible-age.txt     # prints the public key
$EDITOR .sops.yaml

# 2. Re-encrypt the Ansible files to the new recipient set
for f in ansible/group_vars/*/vault.sops.yaml; do sops updatekeys -y "$f"; done

# 3. Swap the private half into the runner's Secret, then shred the temp file
sops apps/ansible-runner/secret.yaml   # replace sops-age-key with /tmp/ansible-age.txt's AGE-SECRET-KEY line
shred -u /tmp/ansible-age.txt

# 4. Commit + reconcile
git add .sops.yaml ansible/ apps/ansible-runner/secret.yaml
git commit -m "chore(ansible-runner): rotate the ansible age key"
git push
flux --context home-k3s reconcile kustomization apps --with-source
```

## Verifying drift detection

On hp-victus, intentionally break the desired state (the time-sync role keeps timesyncd started):

```bash
ssh kblack0610@192.168.1.243 'sudo systemctl stop systemd-timesyncd'
```

Trigger the Job manually. Expect the log to show `changed=1` or a `--diff` section for the service task. Re-start the service:

```bash
ssh kblack0610@192.168.1.243 'sudo systemctl start systemd-timesyncd'
```

Next run: `changed=0` (or 1 for the cosmetic token-fetch task).

## ConvergenceDrift

After each run the job writes every host's PLAY RECAP counts to `/var/lib/node-exporter-textfile/ansible-convergence.prom` on the node its pod ran on: `ansible_convergence_recap{host,status}` and `ansible_convergence_run_timestamp_seconds{host}`. The alert (`apps/monitoring/prometheus-rules-jobs.yaml`, warning, `fleet: fix`) fires when a host's newest run, under 2 days old, has `changed > 0`. Files from older runs stay on other nodes; the rule ignores them by timestamp.

A firing alert means the host is not in the state git describes, and the next ansible-playbook apply would change it. On 2026-11-10 razer still ran a stale `linux-ci-docker-cleanup.service` without `ci-disk-prune.sh` and filled its disk to 91%.

To clear it:

1. Read the latest run's changed tasks: `kubectl -n ansible-runner logs job/<latest> | grep -B3 '^changed:'`. Kubelet rotates long logs, so if the start is cut off, run the check locally: `cd ansible && ansible-playbook playbooks/site.yml --limit <host> --check --diff`.
2. For each task, decide which side is wrong. If the host drifted, apply the role (`--limit <host> --tags <role>`, or Option A/B above). A changed `actions.runner.*.service` or runner `.env` restarts every slot on that host and kills its running job, so apply it once `pgrep -c -x Runner.Worker` on the host prints 0. If the task reports a change in check mode on a converged host, fix the task (`changed_when`, `check_mode: false` on a read-only probe) in a PR.
3. Trigger a dry-run (above). The alert resolves once the new run reports `changed=0` for that host.

## Operations

```bash
# Show recent job runs
kubectl -n ansible-runner get jobs --sort-by=.metadata.creationTimestamp

# Tail logs of the latest run
kubectl -n ansible-runner logs -l app.kubernetes.io/name=ansible-runner --tail=200

# Pause the scheduler (e.g., during planned host maintenance)
kubectl -n ansible-runner patch cronjob convergence-check -p '{"spec":{"suspend":true}}'

# Resume
kubectl -n ansible-runner patch cronjob convergence-check -p '{"spec":{"suspend":false}}'

# Temporarily run every 5 minutes for debugging (revert after!)
kubectl -n ansible-runner patch cronjob convergence-check -p '{"spec":{"schedule":"*/5 * * * *"}}'
kubectl -n ansible-runner patch cronjob convergence-check -p '{"spec":{"schedule":"0 4 * * *"}}'
```

## Rollback

Remove `- ansible-runner` from `apps/kustomization.yaml`, commit, reconcile. Flux prunes the namespace (CronJob, jobs, secret all cascade out). Manual `ansible-playbook` from the workstation continues to work unchanged.

## Related

- `ansible/` — the playbook + role the CronJob runs
- `docs/ansible.md` — layer split (PXE / Ansible / Flux) and general workflow
- `docs/homelab-catalog.md` — inventory of bare-metal vs in-cluster services
