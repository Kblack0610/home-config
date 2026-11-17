# feed-ingest

One service turns social and news sources into scored items. Each item goes to one Postgres ledger (database `feeds`) and onto MQTT, so trader, Home Assistant and ntfy each subscribe instead of each polling the sources themselves. Items feed scored studies (trader `calls`, plan easy-trade-to-start-cozy-russell Phase 4), never trades.

## What runs

| File | What it is |
|---|---|
| `ingest.py` | The ingester: sources, pipeline, routes and `/metrics` on 9100 |
| `config.toml` | Content: sources and cadences, the watchlist, routes, the EDGAR User-Agent |
| `test_ingest.py` | Offline tests, run by `.forgejo/workflows/checks.yaml`; `FEED_TEST_DSN` adds a real-Postgres ledger test |
| `feed-ingest-secret.sops.yaml` | `DATABASE_URL` (role `feed_ingest`) and `LITELLM_API_KEY` (key `feed-ingest`, `apps/litellm/README.md`) |
| `apps/postgres/feed-ingest-db-bootstrap-job.yaml` | Creates the `feeds` database and roles `feed_ingest` (owner) and `feed_reader` (read-only) |
| `apps/freqtrade/trader-feeds-secret.sops.yaml` | `FEED_DATABASE_URL` for trader, as `feed_reader` |
| `apps/monitoring/prometheus-rules-feed-ingest.yaml` | Silent-source and missing-metrics alerts |

## Pipeline

Every source yields one envelope: `source, id, ts, url, text, entities`. Then, in order:

1. Tier-1 rules: a source with `keep = "watchlist"` drops anything no watchlist ticker, name, CIK or handle matches. Such an item never reaches the ledger or the model.
2. Dedup: `INSERT ... ON CONFLICT (source, id) DO NOTHING`. A duplicate stops here and is published nowhere.
3. Tier-2 tags: LiteLLM `fast` returns `tickers, entities, event_type, sentiment`. It is skipped for a source with `tag = false`. If it fails, the item is published with `tags: null` and the failure is counted, so a degraded tag never passes for a real one.
4. Publish to MQTT `feeds/<source>/<ticker or all>` at QoS 1, then to each `[[routes]]` entry whose `match` passes.

Studies take feature times from `feed_items.ingested_at`, the server clock at insert, never from the source's own `ts`, to avoid look-ahead.

| Source | How | Cadence | Keep | Tagged |
|---|---|---|---|---|
| `edgar` | SEC "current events" 8-K Atom feed | 2 min | all | yes |
| `gdelt` | GDELT 2.0 GKG batch from `lastupdate.txt`; matches title and organizations | 5 min (batches are 15) | watchlist | yes |
| `bluesky` | Jetstream WebSocket, post creates, resumes from `feed_cursors` | stream | watchlist | no |
| `rss` | The feeds listed in config | 5 min | all | yes |

## Consumers

| Consumer | How it subscribes |
|---|---|
| MQTT | `mosquitto_sub -h mosquitto.mosquitto.svc.cluster.local -t 'feeds/#' -v`. mqtt2prom ignores `feeds/*`, because entity lists would mint a metric name per index, as Frigate's did. |
| Home Assistant | Route `ha` announces `sensor.feed_ingest_feed_<source>_latest` (HA joins device name `feed-ingest` and entity name `Feed <source> latest`) through MQTT discovery (retained). Its state is the newest watchlist headline and its attributes are the whole item; automations trigger on it, with no HA package edit. |
| ntfy | Route `ntfy-feeds` posts to topic `feeds` at priority 2, only for watchlist items tagged management, M&A, legal, regulatory, earnings or guidance with abs(sentiment) of at least 0.5. The `agents` pager budget is untouched. |
| trader | Reads `feed_items` with `FEED_DATABASE_URL` from Secret `freqtrade/trader-feeds`. |

A new consumer is a `[[routes]]` entry (kinds `ha`, `ntfy`) or an MQTT subscription. A new route kind is code in `Publisher`.

## Change it

- Add a ticker: edit `[watchlist]` in `config.toml`. The ticker matches in capitals or as a cashtag, a name in any case, and a CIK exactly (EDGAR).
- Add a source: write a parser returning `Envelope`s, branch on it in `poll_loop`, add `[sources.<name>]` with `interval_s` and `keep`, and add a fixture test.
- X (pay per read) and Reddit (needs API approval) are off until the human decides.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `FeedSourceSilent` for `gdelt` | The poll reads static files, so it is GDELT down or the egress. `lastupdate.txt` names a batch before its file is served (404 for 15+ minutes), so the poll reads every served batch after its cursor, from the listed batch and the 7 before it (`GDELT_LAG_BATCHES`), and retries an unserved one next poll. It fails when the newest batch read is over 2 hours old (2026-11-17). The DOC API (`api.gdeltproject.org`) answers this IP 429 even on single spaced requests (2026-11-14), which is why it is not used. |
| `FeedSourceSilent` for `edgar` | SEC blocks a request without a declared contact User-Agent. `user_agent` in `config.toml` must carry an email. |
| Items arrive with `tags: null` | `feed_ingest_tag_errors_total` is rising: the `feed-ingest` key is over its $10 / 30d cap, or the gateway is down. Check `/key/info` (`apps/litellm/README.md`). |
| `tag failed ... empty reply (finish_reason length)` | `fast` is a reasoning model and spent `max_tokens` thinking before it answered. Raise `max_tokens` in `Tagger.tag` (1000 since 2026-11-14; 300 failed on long 8-Ks). |
| Pod restarts during start | The pip install of three wheels runs on every start. The startup probe allows 5 minutes. |
| The ledger refuses writes after a password rotation | Rotate the `feed_ingest` password in both `apps/postgres/feed-ingest-db-secret.sops.yaml` and `feed-ingest-secret.sops.yaml`, then bump the bootstrap Job's name. |
