# freqtrade

A Freqtrade dry-run league: 12 paper-trading bots (4 strategies x 3 exchanges) plus weekly backtest experiments and a daily leaderboard. No real money: every bot starts through `lab/league.py`, which refuses anything but key-less spot dry run, and no exchange keys exist anywhere.

Strategy code and the lab scripts live in github.com/Kblack0610/freqtrade_trading_attempt (its README has the troubleshooting table). Pods clone it at the commit pinned in `kustomization.yaml` (`freqtrade-repo` ref).

## What runs

| Resource | What it does |
|---|---|
| StatefulSet `freqtrade-league` | Pod N runs `roster.tsv` row N; sqlite trade history on its own 1Gi PVC; API at `freqtrade-league-N.freqtrade-league:8080` (cluster only) |
| CronJob `freqtrade-experiments` | Sunday 04:00: backtest every strategy on binanceus and coinbase over 90 days, lookahead check, hyperopt the top 3; results on PVC `freqtrade-lab` |
| CronJob `freqtrade-report` | Daily 06:30: reads every bot's API, appends `league-history.jsonl`, writes `reports/<date>.md`; fails if a bot is not dry run |
| Secret `freqtrade-api` | SOPS: API password, JWT secret, ws token shared by the bots and the report |

All on amd64 nodes only.

## Common tasks

Read the latest report and experiments summary:

```sh
kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-report report-manual-$(date +%s)
kubectl --context home-k3s -n freqtrade logs -f job/<that job>
```

The report job prints the leaderboard. Files on the `freqtrade-lab` PVC: `reports/<date>.md`, `runs/latest/summary.md`, `league-history.jsonl`.

Run experiments now: `kubectl --context home-k3s -n freqtrade create job --from=cronjob/freqtrade-experiments exp-manual-$(date +%s)`.

Try a new strategy live: append a row to `roster.tsv` (`name exchange strategy`), raise `replicas` in `statefulset.yaml` to the row count, commit, push to forgejo. Never reorder or delete rows in the middle: row N is bound to pod N's PVC, so a shift hands a bot another bot's trade history. To retire a bot, replace its row in place with a new name and delete that pod's PVC.

Ship a strategy change: merge it in the strategy repo, then set `ref` in `kustomization.yaml` to the new commit. The roster and repo ConfigMaps are hashed, so either change restarts the bots.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Pod logs `pod ordinal N has no roster row` | replicas above the roster row count | Match `replicas` to `roster.tsv` |
| Pod logs `refusing to start` | Something made the config non-dry-run or added a key | Find and remove it; the guard is the point |
| Init container `clone-repo` fails | GitHub unreachable or `ref` not on the remote | Check the SHA exists in the strategy repo |
| Report rows show `URLError` | Bot not Ready yet, or crash-looping | `kubectl -n freqtrade logs freqtrade-league-N` |
| Both CronJobs pending | `freqtrade-lab` is local-path RWO, pinned to the node that first bound it | That node must be up; it is always an amd64 node |
