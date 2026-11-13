# searxng

The one search client for the cluster (namespace `home-assistant`, in-cluster only, no ingress). Home Assistant's agent and binks-api both query it.

## Engines

A query with no `engines=` runs only the free scraped engines (today only Bing answers from the home IP; the others hit CAPTCHAs or 429s). Every search API below is `disabled: true`, so a default query never spends its quota; binks-api names one per search with `engines=<name>`, picked by its search router from `SEARCH_PROVIDERS` in `apps/binks-api/deployment.yaml`, which also holds each one's quota and monthly dollar cap.

| Engine | Free tier | Paid per 1k | Key | Signup |
|---|---|---|---|---|
| linkup | $20 credit a month (about 4,000 searches), 10 qps | $5 | yes | agent |
| firecrawl | 1,000 credits a month (about 500 searches), 10 a minute | about $7.60 | yes | agent |
| tavily | 1,000 a month | $8 | not yet | human (CAPTCHA) |
| you | $100 one-time (about 20,000) | $5 | not yet | human (terms bar scripted signup) |
| serper | 2,500 one-time, Google results | $1 prepaid | not yet | human (CAPTCHA) |
| exa | $10 credit a month (about 2,500) | $4 lite, $7 fast | not yet | human (CAPTCHA) |

Read 2026-11-13 on each provider's pricing page. No account has a card on file, so the worst case past a free tier is an error, never a bill. Terms notes sit beside each engine in `settings.yml`.

## Keys

Keys live in `secret.sops.yaml` (secret `searxng`) as `<NAME>_API_KEY`. `settings.yml` holds only `@<NAME>_API_KEY@` placeholders; the `render-settings` init container fills them into an emptyDir copy at start. A provider with no key keeps its placeholder and fails auth if picked; the router counts that as a miss and moves to the next provider. `scripts/check-secret-store.py` fails CI on a plain key anywhere under this directory.

Account logins (email, password, dashboard) are in `accounts.sops.yaml`, which no kustomization reads.

To add a key: `sops apps/searxng/secret.sops.yaml`, add `<NAME>_API_KEY`, then add the env entry to the init container in `deployment.yaml` if it is a new provider. Flux rolls the pod.

## Check

```
kubectl -n home-assistant exec deploy/searxng -- wget -qO- "http://localhost:8080/search?q=asrock+b650+pg+lightning&format=json&engines=linkup"
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| An API engine answers `HTTP error` on some queries | `json_engine` formats the raw query into the POST body, so a `"` or `\` breaks the JSON (upstream, read 2026-11-13) | binks-api's router strips those characters before it names an API engine |
