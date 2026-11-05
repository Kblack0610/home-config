# freqtrade

A Freqtrade dry-run league: 12 paper-trading bots (4 strategies x 3 exchanges) plus a nightly analysis that backtests every strategy over all backfilled history and ranks them on https://trader.kblab.me. No real money: every bot starts through `lab/league.py`, which refuses anything but key-less spot dry run, and no exchange keys exist anywhere.

Strategy code and the lab scripts live in github.com/Kblack0610/freqtrade_trading_attempt (its README has the troubleshooting table). Its CI builds `ghcr.io/kblack0610/trader` (Freqtrade 2026.9 plus psycopg plus the repo at `/repo`), and every pod here runs that image at the digest pinned in `kustomization.yaml` (`images`, name `trader`).

Results live in the `trader` database in the shared Postgres (`apps/postgres/trader-db-bootstrap-job.yaml` makes the role and database; the schema comes from the image's `lab/migrations/`), so the nightly `pg_dumpall` backs them up.

## What runs

| Resource | What it does |
|---|---|
| StatefulSet `freqtrade-league` | Pod N runs `roster.tsv` row N; sqlite trade history on its own 1Gi PVC; API at `freqtrade-league-N.freqtrade-league:8080` (cluster only) |
| CronJob `freqtrade-backfill` | Hourly at :20: price history for every exchange x pair x timeframe into `data/<exchange>/` on PVC `freqtrade-lab`, prepended to the oldest candle once, then appended; Kraken collected forward from its newest 720 candles; coverage into `data_coverage` |
| CronJob `freqtrade-analysis` | Nightly 02:45: every strategy x exchange (binanceus, coinbase) backtested per calendar year over the whole history, cached per year, lookahead and recursive bias checks on file change, then windows, intervals, estimates, regimes and correlations into the trader database; fails when more than 10% of the chunks it ran failed |
| CronJob `freqtrade-ingest` | Hourly at :07: init container `migrate` applies new schema files, then every bot's trades and a snapshot go into Postgres; fails if a bot is not dry run |
| CronJob `freqtrade-lab-backup` | Sunday 02:50: tar of PVC `freqtrade-lab` to the NAS public share `backups/freqtrade/`, newest 4 kept |
| Secret `freqtrade-api` | SOPS: API password, JWT secret, ws token shared by the bots and the ingest |
| Deployment `trader-web`, Ingress `trader-web` | https://trader.kblab.me: the strategy repo's `web/` site (Overview, bots, strategies, data coverage, analysis runs), read-only as the `trader_web` role; public through the tunnel with crowdsec, every page behind Sign in with Forgejo for `ALLOWED_USERS` |
| Secret `trader-db` | SOPS: `database-url` for the trader role and `web-database-url` for the read-only `trader_web` role; the same passwords are in `apps/postgres/trader-db-secret.sops.yaml` (`trader-db-pass`, `trader-web-db-pass`), rotate each pair together |
| Secret `trader-web-oauth` | SOPS: the Forgejo OAuth2 app `trader.kblab.me` (id 8 under kblack0610, redirect `https://trader.kblab.me/oauth/callback`) and the cookie `session-key`; rotating the key signs everyone out |

All on amd64 nodes only.

## Common tasks

Results are on https://trader.kblab.me: Strategies is the leaderboard, Runs the nightly analysis runs and their failed chunks. The old `reports/`, `runs/` and `league-history.jsonl` files on the `freqtrade-lab` PVC are kept but nothing reads them any more.

Query the database: `kubectl --context home-k3s -n databases exec -it deploy/postgres -- psql -U postgres -d trader`, then for example `SELECT bot, count(*) FROM bot_trades GROUP BY 1;` or the latest snapshot per bot: `SELECT DISTINCT ON (bot) bot, ts, closed_trades, profit_closed_pct, error FROM bot_snapshots ORDER BY bot, ts DESC;`.

Run ingest now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-ingest ingest-manual-$(date +%s)`.

Data coverage: `SELECT exchange, timeframe, count(*), min(first_ts), max(last_ts), sum(gaps), bool_and(oldest_reached) FROM data_coverage GROUP BY 1, 2 ORDER BY 1, 2;`. Run backfill now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-backfill backfill-manual-$(date +%s)`.

Restore the lab PVC: list the archives with `smbclient //nas.nas.svc.cluster.local/public -N -c 'cd backups/freqtrade; ls'` from a pod (or `/mnt/nas/public/backups/freqtrade` on the NAS host), suspend the freqtrade CronJobs (`kubectl -n freqtrade patch cronjob <name> -p '{"spec":{"suspend":true}}'`), untar the newest into the PVC root from a pod that mounts it, then unsuspend. The trader database is restored from the nightly `pg_dumpall` instead.

Run the analysis now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-analysis analysis-manual-$(date +%s)`; its log prints progress every 25 chunks. One strategy only: add `ANALYZE_STRATEGIES` to the job, or run `python /repo/lab/analyze.py bias-one lookahead <Strategy>` in a pod that mounts the PVC.

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
| trader.kblab.me: `Not allowed` after signing in | The Forgejo login is not in `ALLOWED_USERS` | Add it in `web-deployment.yaml` |
| `trader-web` crashloops with `trader-web: missing ...` | A key of Secret `trader-web-oauth` is empty | Re-encrypt `trader-web-oauth-secret.sops.yaml` with all three keys |
| `permission denied for table ...` in the `trader-web` log | A table was created by a role other than `trader`, so the default SELECT grant missed it | Re-run the bootstrap Job (bump its name); it grants SELECT on every table |
| Pod logs `pod ordinal N has no roster row` | replicas above the roster row count | Match `replicas` to `roster.tsv` |
| Pod logs `refusing to start` | Something made the config non-dry-run or added a key | Find and remove it; the guard is the point |
| `ImagePullBackOff` on `ghcr.io/kblack0610/trader` | Digest not pushed, or the GHCR package is private | Re-run the digest query above; a 401 there means the package went private |
| Init container `migrate` fails `changed after it was applied` | An applied migration file was edited in the strategy repo | Revert it there and add the next numbered file |
| `trader-db-bootstrap` fails, or ingest says `password authentication failed` | The two trader-db secrets disagree | Set the same password in both, then bump the Job name to re-run it |
| An ingest snapshot has `URLError` | Bot not Ready yet, or crash-looping | `kubectl -n freqtrade logs freqtrade-league-N` |
| CronJobs pending | `freqtrade-lab` is local-path RWO, pinned to the node that first bound it (hp-victus) | That node must be up; it is always an amd64 node |
| `freqtrade-analysis` OOMKilled | Six backtests at once outgrew the 12Gi limit, for example after a whitelist grew | Lower `ANALYZE_WORKERS` in `analysis-cronjob.yaml`; finished chunks are kept, so the rerun resumes |
| `freqtrade-analysis` hit its deadline | A first run or a freqtrade bump redoes every year | Finished chunks are stored as they complete; the next night carries on |
