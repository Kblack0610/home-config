# freqtrade

A Freqtrade dry-run league: 12 paper-trading bots (4 strategies x 3 exchanges) plus a nightly analysis that backtests every strategy over all backfilled history and ranks them on https://trader.kblab.me. No real money: every bot starts through `lab/league.py`, which refuses anything but key-less spot dry run, and no exchange keys exist anywhere.

Strategy code and the lab scripts live in github.com/Kblack0610/freqtrade_trading_attempt (its README has the troubleshooting table). Its CI builds `ghcr.io/kblack0610/trader` (Freqtrade 2026.9 plus psycopg plus the repo at `/repo`), and every pod here runs that image at the digest pinned in `kustomization.yaml` (`images`, name `trader`).

Results live in the `trader` database in the shared Postgres (`apps/postgres/trader-db-bootstrap-job.yaml` makes the role and database; the schema comes from the image's `lab/migrations/`), so the nightly `pg_dumpall` backs them up.

## What runs

| Resource | What it does |
|---|---|
| StatefulSet `freqtrade-league` | Pod N runs `roster.tsv` row N; sqlite trade history on its own 1Gi PVC; API at `freqtrade-league-N.freqtrade-league:8080` (cluster only) |
| CronJob `freqtrade-backfill` | Hourly at :20: price history for every exchange x pair x timeframe into `data/<exchange>/` on PVC `freqtrade-lab`, prepended to the oldest candle once, then appended; Kraken collected forward from its newest 720 candles; coverage into `data_coverage` |
| CronJob `freqtrade-backfill-research` | Hourly at :50: the same backfill for the research venues okx and kucoin (5m and up, no 1m), in its own job so OKX's slow first pass never delays the bot venues |
| CronJob `freqtrade-analysis` | Nightly 02:45: every strategy x exchange (binanceus, coinbase) backtested per calendar year over the whole history, cached per year, lookahead and recursive bias checks on file change, then windows, intervals, estimates, regimes and correlations into the trader database; fails when more than 10% of the chunks it ran failed |
| CronJob `freqtrade-walkforward` | Nightly 01:00, ending by 09:00 UTC: for each of the last 12 whole months, hyperopt tunes each of up to 16 candidates (league strategies, promote and watch suggestions, then each venue's best year, lookahead-clean) on the 180 days before it, on binanceus, okx and kucoin once each venue is fully backfilled, then the unseen month is backtested as is and tuned; each fold is stored as it finishes and skipped once stored, so a first run spans several nights, each starting no fold after `WF_BUDGET_S` (4.5 h) and recording `folds_left`, retune suggestions follow; fails when more than 10% of the months it ran failed |
| CronJob `freqtrade-ingest` | Every 5 minutes: init container `migrate` applies new schema files, then every bot's trades and a snapshot go into Postgres; fails if a bot is not dry run |
| CronJob `trader-markets-recorder` | Every 5 min: one sweep of every open Kalshi market (strategy repo `docs/markets.md`, Recorder); changed quotes as parquet into MinIO bucket `trader-markets` (`apps/minio/trader-markets-bucket-job.yaml`), market metadata, resolutions and a `market_sweeps` row into the trader database. Off hp-victus by node affinity. Last sweeps: `SELECT started_at, seen, changes, resolved, ok, error FROM market_sweeps ORDER BY id DESC LIMIT 5;` |
| CronJobs `trader-markets-recorder-<venue>` | The same recorder for one other venue each, by name (`markets-venue-recorders-cronjob.yaml`): `polymarket-us`, `predictit` and `manifold` every 5 min, `polymarket` (global, about 10 min a sweep) every 15 min. The Odds API has none until its key and cadence are decided. Run one now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/trader-markets-recorder-manifold rec-manual-$(date +%s)` |
| CronJob `trader-markets-compact` | Nightly 01:30 UTC: folds each settled day's recorder parts in MinIO bucket `trader-markets` into one `quotes/venue=<v>/date=<d>/day.parquet` and deletes the parts (strategy repo `docs/markets.md`, Recorder). MinIO only. |
| Secret `trader-markets-s3` | SOPS: the recorder's MinIO keys, user `trader-markets` limited to its bucket; the same values are in `apps/minio/trader-markets-s3-secret.sops.yaml`, rotate both together |
| CronJob `freqtrade-lab-backup` | Sunday 02:50: tar of PVC `freqtrade-lab` to the NAS public share `backups/freqtrade/`, newest 4 kept |
| Deployment `freqtrade-watcher` | Every 60 s: each bot's marked-to-market equity into the `watch` table, for the daily loss alert and the kill drill (strategy repo README, Watcher). Stage 0: alerts only. Holds no exchange key |
| Secret `freqtrade-api` | SOPS: API password, JWT secret, ws token shared by the bots and the ingest |
| Deployment `trader-web`, Ingress `trader-web` | https://trader.kblab.me: the strategy repo's `web/` site (Overview, bots, strategies, data coverage, analysis runs), read-only as the `trader_web` role; public through the tunnel with crowdsec, every page behind Sign in with Forgejo for `ALLOWED_USERS` |
| ServiceMonitor `trader-web` | Scrapes the site's exporter on port 9100 (not routed by the Ingress) every minute: per-bot dry-run flag, API errors and profit, ingest and candle age, run outcomes, open suggestions, event-contract recorder sweeps per venue, cross-venue links and paper league equity. Alerts in `apps/monitoring/prometheus-rules-freqtrade.yaml` (TraderBotNotDryRun pages; TraderMarketsRecorderStale, TraderMarketsSweepSlow, TraderMarketsGlobalSweepSlow, TraderMarketsAdapterErrors and TraderPaperDrawdown warn), Grafana dashboard "Freqtrade league and lab", Markets row included |
| Secret `trader-db` | SOPS: `database-url` for the trader role and `web-database-url` for the read-only `trader_web` role; the same passwords are in `apps/postgres/trader-db-secret.sops.yaml` (`trader-db-pass`, `trader-web-db-pass`), rotate each pair together |
| Secret `trader-web-oauth` | SOPS: the Forgejo OAuth2 app `trader.kblab.me` (id 8 under kblack0610, redirect `https://trader.kblab.me/oauth/callback`) and the cookie `session-key`; rotating the key signs everyone out |

All on amd64 nodes only.

## Common tasks

Results are on https://trader.kblab.me: Strategies is the leaderboard, Runs the nightly analysis runs and their failed chunks. The old `reports/`, `runs/` and `league-history.jsonl` files on the `freqtrade-lab` PVC are kept but nothing reads them any more.

Query the database: `kubectl --context home-k3s -n databases exec -it deploy/postgres -- psql -U postgres -d trader`, then for example `SELECT bot, count(*) FROM bot_trades GROUP BY 1;` or the latest snapshot per bot: `SELECT DISTINCT ON (bot) bot, ts, closed_trades, profit_closed_pct, error FROM bot_snapshots ORDER BY bot, ts DESC;`.

Run ingest now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-ingest ingest-manual-$(date +%s)`.

Data coverage: `SELECT exchange, timeframe, count(*), min(first_ts), max(last_ts), sum(gaps), bool_and(oldest_reached) FROM data_coverage GROUP BY 1, 2 ORDER BY 1, 2;`. Run backfill now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-backfill backfill-manual-$(date +%s)` (research venues: `--from=cronjob/freqtrade-backfill-research`).

Restore the lab PVC: list the archives with `smbclient //nas.nas.svc.cluster.local/public -N -c 'cd backups/freqtrade; ls'` from a pod (or `/mnt/nas/public/backups/freqtrade` on the NAS host), suspend the freqtrade CronJobs (`kubectl -n freqtrade patch cronjob <name> -p '{"spec":{"suspend":true}}'`), untar the newest into the PVC root from a pod that mounts it, then unsuspend. The trader database is restored from the nightly `pg_dumpall` instead.

Run the analysis now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-analysis analysis-manual-$(date +%s)`; its log prints progress every 25 chunks. One strategy only: add `ANALYZE_STRATEGIES` to the job, or run `python /repo/lab/analyze.py bias-one lookahead <Strategy>` in a pod that mounts the PVC.

Run the walk-forward now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-walkforward walkforward-manual-$(date +%s)`; its log prints a line per month. Results are on the site's Walk-forward page.

Try a new strategy live: append a row to `roster.tsv` (`name exchange strategy`), raise `replicas` in `statefulset.yaml` to the row count, commit, push to forgejo. Never reorder or delete rows in the middle: row N is bound to pod N's PVC, so a shift hands a bot another bot's trade history. To retire a bot, replace its row in place with a new name and delete that pod's PVC.

Ship a strategy or lab change: merge it in the strategy repo, wait for its `image` workflow to push, then set `digest` in `kustomization.yaml` to the digest of tag `sha-<first 7 of the merge commit>`. The package is public, so an anonymous query works:

```sh
TOK=$(curl -s 'https://ghcr.io/token?scope=repository:kblack0610/trader:pull' | jq -r .token)
curl -sI -H "Authorization: Bearer $TOK" -H 'Accept: application/vnd.oci.image.index.v1+json' \
  https://ghcr.io/v2/kblack0610/trader/manifests/sha-<7> | grep -i docker-content-digest
```

A new digest or roster restarts the bots; their trade history is on their PVCs and in Postgres.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| trader.kblab.me: Forgejo says the redirect URI is invalid | The OAuth2 app's redirect is not `https://trader.kblab.me/oauth/callback` | Fix it under Forgejo Settings, Applications, `trader.kblab.me` |
| trader.kblab.me: `Sign-in expired or was not started here` on every try, usually from a phone on the home Wi-Fi | The page was opened as `http://`: on the LAN the host answers plain http, and the browser drops the Secure state cookie `/login` sets there | The Ingress redirects http to https (`kube-system-redirect-https`); traefik access logs show `entryPointName: web` for the `/login` hit if it comes back |
| trader.kblab.me: `Not allowed` after signing in | The Forgejo login is not in `ALLOWED_USERS` | Add it in `web-deployment.yaml` |
| `trader-web` crashloops with `trader-web: missing ...` | A key of Secret `trader-web-oauth` is empty | Re-encrypt `trader-web-oauth-secret.sops.yaml` with all three keys |
| `permission denied for table ...` in the `trader-web` log | A table was created by a role other than `trader`, so the default SELECT grant missed it | Re-run the bootstrap Job (bump its name); it grants SELECT on every table |
| Pod logs `pod ordinal N has no roster row` | replicas above the roster row count | Match `replicas` to `roster.tsv` |
| Pod logs `refusing to start` | Something made the config non-dry-run or added a key | Find and remove it; the guard is the point |
| `ImagePullBackOff` on `ghcr.io/kblack0610/trader` | Digest not pushed, or the GHCR package is private | Re-run the digest query above; a 401 there means the package went private |
| Init container `migrate` fails `changed after it was applied` | An applied migration file was edited in the strategy repo | Revert it there and add the next numbered file |
| `trader-db-bootstrap` fails, or ingest says `password authentication failed` | The two trader-db secrets disagree | Set the same password in both, then bump the Job name to re-run it |
| An ingest snapshot has `URLError` | Bot not Ready yet, or crash-looping | `kubectl -n freqtrade logs freqtrade-league-N` |
| CronJobs pending | `freqtrade-lab` is local-path RWO, pinned to razer-laptop (moved from hp-victus 2026-11-09 with `docs/runbooks/move-local-path-pvc.md`) | That node must be up; to move it again, run that runbook with `-` as the deployment |
| `freqtrade-analysis`, `freqtrade-walkforward` or a backfill (peaks near 2 GiB of its 4Gi) OOMKilled, and `kubectl get events -A --field-selector involvedObject.kind=Node` shows SystemOOM on razer-laptop | A node OOM, not the pod limit: razer-laptop (93Gi) also runs the Unity CI pool in `ci.slice` (MemoryMax 72Gi, mostly page cache: about 3Gi anon on 2026-11-09), beside the analysis and walk-forward (14Gi each, requests equal limits) | Check `systemctl show ci.slice -p MemoryCurrent` and its `memory.stat` anon on razer-laptop; if CI anon memory grew, lower its MemoryMax in Ansible or `WF_MEMORY_GB`, never a request below its limit |
| `freqtrade-analysis` OOMKilled with no node SystemOOM | The tasks in flight outgrew the 14Gi limit (runs peak near 10.3 GiB): 1m work peaks near 4 GB a task, a 5m year of 10 pairs near 1.1 GB | `ANALYZE_MEMORY_GB` (8) caps the estimated memory in flight; lower it, or re-measure the per-candle cost in the strategy repo README; finished chunks are kept, so the rerun resumes |
| `freqtrade-analysis` hit its deadline | A first run or a freqtrade bump redoes every year | Finished chunks are stored as they complete; the next night carries on |
| `freqtrade-walkforward` OOMKilled or hit its deadline | The folds running at once outgrew 14Gi (`WF_MEMORY_GB` estimates them), or the folds still running at `WF_BUDGET_S` plus the post steps outlasted the 8 h deadline (an image without trader #98 has no budget, so a first run or freqtrade bump hits it every night) | Stored folds are skipped, so each night carries on, and TraderRunStale clears once one night finishes. For a deadline lower `WF_BUDGET_S`; for an OOM lower `WF_MEMORY_GB` or `WF_WORKERS` |
| A trader.kblab.me page 500s with `relation ... does not exist` or `column ... does not exist` after an image bump | The new image's migrations had not run; they used to ride only on the job init containers | trader-web runs `migrate.py` as its own init container; if one fails, `kubectl -n freqtrade logs deploy/trader-web -c migrate` says why |
| TraderWatcherDown fires | The `freqtrade-watcher` pod is down or every pass fails (a pass needs the database) | `kubectl -n freqtrade logs deploy/freqtrade-watcher`; a failed pass logs `watcher: pass failed`. Bots keep trading on paper meanwhile |
| TraderBotNotTakingEntries fires | A drill was left on, or a bot was paused by hand | `kubectl -n freqtrade exec deploy/freqtrade-watcher -- python lab/watcher.py --drill-end <bot>`; if it says no drill is running, POST `/start` on that bot |
| TraderMetricsMissing fires | `trader-web` is down, or its exporter cannot read the database, so the not-dry-run page cannot see the bots | `kubectl -n freqtrade logs deploy/trader-web -c web`; `kubectl -n freqtrade port-forward deploy/trader-web 9100` and read `/metrics`: `trader_db_up 0` is a database problem |
| TraderBotNotDryRun fires | A bot's newest snapshot says dry_run false | `flux suspend kustomization apps` (or Flux scales it back on its next sync), then `kubectl -n freqtrade scale sts freqtrade-league --replicas=0`; find and revert the config or roster change, then `flux resume kustomization apps` |
