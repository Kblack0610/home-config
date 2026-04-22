# Ansible Runner

Flux-managed in-cluster runner for Ansible playbooks. Replaces Semaphore UI with a pure-declarative CronJob + Job flow. No UI to click through on first deploy.

## What runs, when

| Job | Schedule | Behavior |
|-----|----------|----------|
| `convergence-check` CronJob | 04:00 daily, America/Los_Angeles | Clones `home-config` at master, runs `ansible-playbook playbooks/site.yml --limit thinkcentre --check --diff`. Logs drift. Does NOT change state. |

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
ANSIBLE_VAULT_PASSWORD_FILE=~/.ansible-vault-pass \
  ansible-playbook playbooks/site.yml --limit thinkcentre
```

(The CronJob is convenient for scheduled drift checks; Option B is fine for ad-hoc applies.)

## Secrets

`apps/ansible-runner/secret.yaml` is SOPS-encrypted and holds:

| Key | Purpose |
|-----|---------|
| `ssh-privkey` | Dedicated ed25519 keypair for the runner. Public key is in `kblack0610@thinkcentre:~/.ssh/authorized_keys`. |
| `ansible-vault-pass` | Decrypts `ansible/group_vars/*/vault.yml`. Same as workstation's `~/.ansible-vault-pass`. |

### Rotating the SSH key

```bash
# 1. Generate new keypair on workstation
ssh-keygen -t ed25519 -N '' -C 'ansible-runner@home-k3s' -f /tmp/newkey

# 2. Install pub key on every target host
cat /tmp/newkey.pub | ssh kblack0610@192.168.1.100 'cat >> ~/.ssh/authorized_keys && sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys'

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

### Rotating the ansible-vault password

```bash
# 1. Re-encrypt all ansible vaults with the new password (repeat per-vault)
ansible-vault rekey ansible/group_vars/linux_bare_metal/vault.yml

# 2. Update the workstation + the SOPS secret
echo -n 'NEW_PASSWORD' > ~/.ansible-vault-pass
sops apps/ansible-runner/secret.yaml   # update ansible-vault-pass

# 3. Commit + reconcile
git add ansible/ apps/ansible-runner/secret.yaml
git commit -m "chore(ansible): rotate vault password"
git push
flux --context home-k3s reconcile kustomization apps --with-source
```

## Verifying drift detection

On thinkcentre, intentionally break the desired state:

```bash
ssh kblack0610@192.168.1.100 'sudo systemctl stop actions.runner.thinkcentre-linux.service'
```

Trigger the Job manually. Expect the log to show `changed=1` or a `--diff` section for the service task. Re-start the service:

```bash
ssh kblack0610@192.168.1.100 'sudo systemctl start actions.runner.thinkcentre-linux.service'
```

Next run: `changed=0` (or 1 for the cosmetic token-fetch task).

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
