# Tax export (actual-http-api bridge + quarterly CSV)

Produces a per-year CSV of categorized transactions on the NAS - the artifact you hand a CPA. Built on an in-cluster `actual-http-api` bridge that wraps Actual's sync protocol in plain HTTP+JSON.

## Status: activation-gated

The manifests exist in `apps/actual-budget/` but are commented out in `apps/actual-budget/kustomization.yaml`. They deploy nothing until you activate them, because the bridge needs your Actual server password, which only you have. This is deliberate - it avoids shipping a non-functional, auth-broken pod into the auto-reconciling GitOps tree.

Components (all in `apps/actual-budget/`):
- `actual-http-api-deployment.yaml` + `actual-http-api-service.yaml` - the REST bridge (`jhonderson/actual-http-api:26.2.0`, ClusterIP :5007).
- `finance-api-secret.yaml` - SOPS secret. `api-key` is a real generated bearer key; `actual-server-password` and `budget-sync-id` are placeholders you fill in.
- `tax-export-cronjob.yaml` - quarterly job (1st of Jan/Apr/Jul/Oct, 04:00) that pulls the year's transactions and pushes `actual-tax-<year>.csv` to `backups/home-k3s/actual-budget/tax-exports/` on the NAS.

## Activation

1. Populate the secret with your real values:
   ```bash
   export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
   sops apps/actual-budget/finance-api-secret.yaml
   ```
   Set `actual-server-password` (the Actual UI password) and `budget-sync-id` (Actual -> Settings -> "Sync ID", a UUID). Leave `api-key` as generated.

2. Uncomment the four resources under the activation block in `apps/actual-budget/kustomization.yaml`.

3. Commit, push to forgejo, reconcile:
   ```bash
   flux reconcile kustomization apps --with-source
   kubectl --context home-k3s -n actual-budget get pods -l app=actual-http-api
   ```

## Run it on demand

```bash
kubectl --context home-k3s create job --from=cronjob/actual-budget-tax-export \
  tax-export-$(date +%s) -n actual-budget
kubectl --context home-k3s -n actual-budget logs -l job-name --tail=40
```

Export a specific past year by setting `TAX_YEAR` on the job (edit the env, or `kubectl create job` then patch). Default is the current calendar year.

## Validate the first run

The export script's jq field paths (`.payee`, `.category`, `.amount`, `.date`) match jhonderson/actual-http-api 26.2.0. Before trusting the CSV:
- Confirm row count roughly matches the transactions you see in the UI for that year.
- Confirm `amount` is in dollars (script divides integer cents by 100) and signs are right (outflows negative).
- Confirm `payee` and `category` resolved to names, not raw UUIDs. If they show UUIDs, the categories/payees lookup call returned an unexpected shape - adjust the `.data[]` jq path to your version's response.

Fetch the CSV off the NAS:
```bash
kubectl --context home-k3s -n nas exec deploy/nas -- \
  ls -la /shares/private/backups/home-k3s/actual-budget/tax-exports/
```

## Note

The bridge caches the downloaded budget in an `emptyDir` - it re-downloads on restart, so no PVC and nothing to back up. If your budget file has a separate end-to-end encryption password (distinct from the server password), the jhonderson API takes it as a per-request parameter; extend the export script's curl calls with the documented `budget-encryption-password` header if so.
