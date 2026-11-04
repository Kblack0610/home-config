# freqtrade

A Freqtrade dry-run league: 12 paper-trading bots (4 strategies x 3 exchanges) plus weekly backtest experiments and a daily leaderboard. No real money: every bot starts through `lab/league.py`, which refuses anything but key-less spot dry run, and no exchange keys exist anywhere.

Strategy code and the lab scripts live in github.com/Kblack0610/freqtrade_trading_attempt (its README has the troubleshooting table). Its CI builds `ghcr.io/kblack0610/trader` (Freqtrade 2026.9 plus psycopg plus the repo at `/repo`), and every pod here runs that image at the digest pinned in `kustomization.yaml` (`images`, name `trader`).

Results live in the `trader` database in the shared Postgres (`apps/postgres/trader-db-bootstrap-job.yaml` makes the role and database; the schema comes from the image's `lab/migrations/`), so the nightly `pg_dumpall` backs them up.

## What runs

| Resource | What it does |
|---|---|
| StatefulSet `freqtrade-league` | Pod N runs `roster.tsv` row N; sqlite trade history on its own 1Gi PVC; API at `freqtrade-league-N.freqtrade-league:8080` (cluster only) |
| CronJob `freqtrade-backfill` | Hourly at :20: price history for every exchange x pair x timeframe into `data/<exchange>/` on PVC `freqtrade-lab`, prepended to the oldest candle once, then appended; Kraken collected forward from its newest 720 candles; coverage into `data_coverage` |
| CronJob `freqtrade-experiments` | Sunday 04:00: backtest every strategy on binanceus and coinbase over 90 days on the backfilled data, lookahead check, hyperopt the top 3; results on PVC `freqtrade-lab` |
| CronJob `freqtrade-ingest` | Hourly at :07: init container `migrate` applies new schema files, then every bot's trades and a snapshot go into Postgres, and `runs/*/summary.json` plus `league-history.jsonl` are imported (idempotent); fails if a bot is not dry run |
| CronJob `freqtrade-report` | Daily 06:30: reads every bot's API, appends `league-history.jsonl`, writes `reports/<date>.md`; fails if a bot is not dry run |
| CronJob `freqtrade-lab-backup` | Sunday 02:50: tar of PVC `freqtrade-lab` to the NAS public share `backups/freqtrade/`, newest 4 kept |
| Secret `freqtrade-api` | SOPS: API password, JWT secret, ws token shared by the bots, the report and the ingest |
| Secret `trader-db` | SOPS: `database-url` for the trader role; the same password is in `apps/postgres/trader-db-secret.sops.yaml`, rotate both together |

All on amd64 nodes only.

## Common tasks

Read the latest report and experiments summary:

```sh
kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-report report-manual-$(date +%s)
kubectl --context home-k3s -n freqtrade logs -f job/<that job>
```

The report job prints the leaderboard. Files on the `freqtrade-lab` PVC: `reports/<date>.md`, `runs/latest/summary.md`, `league-history.jsonl`.

Query the database: `kubectl --context home-k3s -n databases exec -it deploy/postgres -- psql -U postgres -d trader`, then for example `SELECT bot, count(*) FROM bot_trades GROUP BY 1;` or the latest snapshot per bot: `SELECT DISTINCT ON (bot) bot, ts, closed_trades, profit_closed_pct, error FROM bot_snapshots ORDER BY bot, ts DESC;`.

Run ingest now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-ingest ingest-manual-$(date +%s)`.

Data coverage: `SELECT exchange, timeframe, count(*), min(first_ts), max(last_ts), sum(gaps), bool_and(oldest_reached) FROM data_coverage GROUP BY 1, 2 ORDER BY 1, 2;`. Run backfill now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-backfill backfill-manual-$(date +%s)`.

Restore the lab PVC: list the archives with `smbclient //nas.nas.svc.cluster.local/public -N -c 'cd backups/freqtrade; ls'` from a pod (or `/mnt/nas/public/backups/freqtrade` on the NAS host), suspend the freqtrade CronJobs (`kubectl -n freqtrade patch cronjob <name> -p '{"spec":{"suspend":true}}'`), untar the newest into the PVC root from a pod that mounts it, then unsuspend. The trader database is restored from the nightly `pg_dumpall` instead.

Run experiments now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-experiments exp-manual-$(date +%s)`.

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
| Pod logs `pod ordinal N has no roster row` | replicas above the roster row count | Match `replicas` to `roster.tsv` |
| Pod logs `refusing to start` | Something made the config non-dry-run or added a key | Find and remove it; the guard is the point |
| `ImagePullBackOff` on `ghcr.io/kblack0610/trader` | Digest not pushed, or the GHCR package is private | Re-run the digest query above; a 401 there means the package went private |
| Init container `migrate` fails `changed after it was applied` | An applied migration file was edited in the strategy repo | Revert it there and add the next numbered file |
| `trader-db-bootstrap` fails, or ingest says `password authentication failed` | The two trader-db secrets disagree | Set the same password in both, then bump the Job name to re-run it |
| Report rows show `URLError` | Bot not Ready yet, or crash-looping | `kubectl -n freqtrade logs freqtrade-league-N` |
| CronJobs pending | `freqtrade-lab` is local-path RWO, pinned to the node that first bound it | That node must be up; it is always an amd64 node |
