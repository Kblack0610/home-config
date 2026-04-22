# Semaphore UI

Web UI for running Ansible playbooks against bare-metal hosts defined in `ansible/inventory.yml`. Stores run history and logs per execution. LAN-only via the `monitoring-local-network-only` Traefik middleware.

- URL: [https://semaphore.kblab.me](https://semaphore.kblab.me) (LAN only)
- Namespace: `semaphore`
- Storage: SQLite on a 2 Gi local-path PVC (bolt is deprecated upstream; migrate to Postgres if run volume grows)

## Manifests

| File | Purpose |
|------|---------|
| `namespace.yaml` | `semaphore` namespace |
| `pvc.yaml` | `semaphore-data` PVC for BoltDB + workspace |
| `secret.yaml` | Admin creds and access-key encryption (SOPS-encrypted in git) |
| `deployment.yaml` | `semaphoreui/semaphore:v2.17.37-ansible2.16.5`, SQLite DB on PVC |
| `service.yaml` | ClusterIP on :80 → targetPort 3000 |
| `ingress.yaml` | Traefik → `semaphore.kblab.me`, LAN allowlist middleware |

## First deploy

1. **Set the secret values before committing**:

   ```bash
   # Generate a random access-key encryption key
   head -c32 /dev/urandom | base64

   # Edit apps/semaphore/secret.yaml, replace CHANGE_ME_BEFORE_SOPS_ENCRYPT
   # values with a real admin password and the base64 key.

   # Encrypt in place with SOPS
   sops --encrypt --in-place apps/semaphore/secret.yaml
   ```

2. **Add to the root apps kustomization** (`apps/kustomization.yaml`): `- semaphore`.

3. **Commit, push, let Flux reconcile**:

   ```bash
   flux reconcile kustomization apps --with-source
   kubectl -n semaphore rollout status deploy/semaphore --timeout=2m
   ```

4. **Verify ingress**:

   ```bash
   curl -skLI https://semaphore.kblab.me/ | head -1   # expect HTTP/2 200 from LAN
   ```

## First-time Semaphore configuration

1. Log in at `https://semaphore.kblab.me` with `admin` and the password you set.
2. **Projects → New Project** → name: `home-config`.
3. **Key Store → New Key**:
   - SSH private key: paste the workstation's `~/.ssh/id_ed25519` (the one already authorized on thinkcentre and the Macs).
   - Ansible vault password: paste the contents of `~/.ansible-vault-pass`.
4. **Repositories → New Repository**: clone `ssh://git@github.com/Kblack0610/home-config` (use a deploy key or the same SSH key).
5. **Inventory → New Inventory**: static, path `ansible/inventory.yml`.
6. **Environment → New Environment** (empty is fine for Phase A).
7. **Task Templates → New**:
   - Playbook filename: `ansible/playbooks/site.yml`
   - Inventory: the one from step 5
   - Arguments: `--limit thinkcentre`
   - Vault key: the one from step 3
   - Survey / CLI args: toggleable `--check` for dry-runs
8. **Run** once with `--check`, inspect the log, then run without `--check`.

## Operations

```bash
# Pod status
kubectl -n semaphore get pods

# Tail app logs
kubectl -n semaphore logs deploy/semaphore -f
```

### Rotate the admin password

**Important:** `SEMAPHORE_ADMIN_PASSWORD` env var only seeds the admin
user **on first boot when the admin does not yet exist**. Once the admin
row is in SQLite, Semaphore logs `Welcome back, admin! (a user with this
username/email is already set up..)` and ignores the env var. Editing
the SOPS secret + rolling out the pod is therefore *not enough* to
rotate the password — you must also reset it in the DB.

The idiomatic fix:

```bash
# 1. Edit the SOPS secret so git and the pod env stay in sync.
sops apps/semaphore/secret.yaml        # edit SEMAPHORE_ADMIN_PASSWORD
git add apps/semaphore/secret.yaml && git commit -m "chore(semaphore): rotate admin password" && git push
flux --context home-k3s reconcile kustomization apps --with-source

# 2. Force-reset the DB password to match, via Semaphore's own CLI.
#    (pod already has the new env; this command writes it into SQLite.)
NEW_PW=$(sops --decrypt apps/semaphore/secret.yaml | awk '/SEMAPHORE_ADMIN_PASSWORD/ {print $2}')
kubectl --context home-k3s -n semaphore exec deploy/semaphore -- \
  semaphore user change-by-login --login admin --password "$NEW_PW" --config /etc/semaphore/config.json

# 3. Verify
curl -sk -X POST https://semaphore.kblab.me/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"auth\":\"admin\",\"password\":\"$NEW_PW\"}" -w "HTTP %{http_code}\n"
# expect HTTP 204
```

No pod restart needed — `semaphore user change-by-login` writes directly to the
SQLite DB which the running pod reads on next login attempt.

## Upgrade

```bash
# Bump the image tag in deployment.yaml, commit, push, reconcile.
# SQLite migrates between minor versions. Back up the PVC before major bumps.
```

## Rollback

```bash
# Remove `- semaphore` from apps/kustomization.yaml, commit, push.
# Flux prunes the namespace. The PVC is retained per the reclaim policy.
```

## Related

- `ansible/README.md` — the playbook project Semaphore runs
- `docs/gitops.md` — layer split (PXE / Ansible / Flux)
- `docs/homelab-catalog.md` — inventory of bare-metal vs in-cluster services
