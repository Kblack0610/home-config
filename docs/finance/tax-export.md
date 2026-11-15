# Tax export (actual-http-api bridge + quarterly CSV)

Produces a per-year CSV of categorized transactions on the NAS - the artifact you hand a CPA. Built on an in-cluster `actual-http-api` bridge that wraps Actual's sync protocol in plain HTTP+JSON.

## Status: included in GitOps

The bridge and tax-export CronJob are listed in `apps/actual-budget/kustomization.yaml`. Flux owns their deployment on home-k3s. Inclusion in git does not prove a successful live export; validate the CSV before using it for reconciliation.

Components (all in `apps/actual-budget/`):
- `actual-http-api-deployment.yaml` + `actual-http-api-service.yaml` - the REST bridge (`jhonderson/actual-http-api:26.8.1`, ClusterIP :5007).
- `finance-api-secret.yaml` - SOPS secret supplying the API key, Actual server password and budget sync ID.
- `tax-export-cronjob.yaml` - quarterly job (1st of Jan/Apr/Jul/Oct, 04:00) that pulls the year's transactions and pushes `actual-tax-<year>.csv` to `backups/home-k3s/actual-budget/tax-exports/` on the NAS.

## Category-to-tax-line map

[Tax categories](./tax-categories.md#group-business---schedule-c-self-employment-deel-consulting-bnb) owns the mapping used to interpret the CSV. The exporter includes every category and resolves category IDs to their current names; it does not filter the old software category or assign tax lines in code. `Biz: AI`, `Biz: CI & build` and `Biz: Hosting & infra` retain the old software category's 27a mapping in that table.

For the human-applied split, follow the [2026 migration and before/after checks](./tax-categories.md#dev-spend-migration-human-applied). Export twice, keeping the pre-change CSV under a separate private name before the second run overwrites the NAS filename. Compare signed amounts in cents grouped by the documented tax-line mapping, including other 27a categories. This is a private reconciliation, with no transaction upload to the agent or dev-cost platform.

## Run it on demand

```bash
kubectl --context home-k3s create job --from=cronjob/actual-budget-tax-export \
  tax-export-$(date +%s) -n actual-budget
kubectl --context home-k3s -n actual-budget logs -l job-name --tail=40
```

Export a specific past year by setting `TAX_YEAR` on the job (edit the env, or `kubectl create job` then patch). Default is the current calendar year.

## Validate the first run

The export script's jq field paths (`.payee`, `.category`, `.amount`, `.date`) were written for jhonderson/actual-http-api 26.2.0 and have not been rechecked against the pinned 26.8.1 bridge. Before trusting the CSV:
- Confirm row count roughly matches the transactions you see in the UI for that year.
- Confirm `amount` is in dollars (script divides integer cents by 100) and signs are right (outflows negative).
- Confirm `payee` and `category` resolved to names. If they show UUIDs, the categories/payees lookup call returned an unexpected shape - adjust the `.data[]` jq path to your version's response.

Fetch the CSV off the NAS:
```bash
kubectl --context home-k3s -n nas exec deploy/nas -- \
  ls -la /shares/private/backups/home-k3s/actual-budget/tax-exports/
```

## Note

The bridge caches the downloaded budget in an `emptyDir` - it re-downloads on restart, so no PVC and nothing to back up. If your budget file has a separate end-to-end encryption password (distinct from the server password), the jhonderson API takes it as a per-request parameter; extend the export script's curl calls with the documented `budget-encryption-password` header if so.
