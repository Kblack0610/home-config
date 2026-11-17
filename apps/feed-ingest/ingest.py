"""feed-ingest: social and news sources -> one envelope -> Postgres ledger -> MQTT and routes.

Each source yields Envelope(source, id, ts, url, text, entities). The pipeline then runs, in order:
  1. tier-1 rules: the source's `keep` mode ("all", or "watchlist" = a watchlist ticker, name or
     handle must match), so a firehose never reaches the ledger or the model;
  2. dedup: INSERT ... ON CONFLICT DO NOTHING on (source, id); a duplicate stops here;
  3. tier-2 tags: LiteLLM `fast` returns tickers, entities, event_type, sentiment (scoped key);
  4. publish: MQTT feeds/<source>/<topic> (QoS 1), then every config route whose match passes.

Sources, watchlist and routes are config.toml, so a new consumer is a route or an MQTT
subscription, not code. The ledger is the source of truth; MQTT is transport. Feature times for
any study are `ingested_at` (server clock at insert), never the source's own `ts`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import email.utils
import json
import logging
import os
import queue
import re
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("feed-ingest")

ATOM = "{http://www.w3.org/2005/Atom}"
HTTP_TIMEOUT_S = 30
MAX_CONSECUTIVE_FAILURES = 20


@dataclasses.dataclass
class Envelope:
    source: str
    id: str
    ts: dt.datetime | None
    url: str
    text: str
    entities: dict = dataclasses.field(default_factory=dict)
    tags: dict | None = None
    topic: str = "all"

    def payload(self, ingested_at: dt.datetime) -> dict:
        return {
            "source": self.source,
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "ingested_at": ingested_at.isoformat(),
            "url": self.url,
            "text": self.text,
            "entities": self.entities,
            "tags": self.tags,
        }




class Watchlist:
    """Tickers with their names and CIKs, plus Bluesky handles. Matching is the tier-1 rule."""

    def __init__(self, cfg: dict):
        self.tickers = [dict(t) for t in cfg.get("tickers", [])]
        self.handles = [h.lower() for h in cfg.get("handles", [])]
        self.dids: set[str] = set()  # the handles' DIDs, resolved at start (Jetstream names authors by DID)
        self._name_re = []
        for t in self.tickers:
            # The ticker matches in capitals only (ARM, not "arm"), a name in any case.
            self._name_re.append((t["ticker"], re.compile(rf"(?<![A-Za-z0-9])\$?{re.escape(t['ticker'])}(?![A-Za-z0-9])")))
            if t.get("names"):
                names = "|".join(re.escape(n) for n in t["names"])
                self._name_re.append((t["ticker"], re.compile(rf"(?<![A-Za-z0-9])(?:{names})(?![A-Za-z0-9])", re.I)))

    def match(self, env: Envelope) -> list[str]:
        """Tickers this item is about, by CIK, name, ticker or cashtag. Sorted, unique."""
        hits = set()
        cik = str(env.entities.get("cik", "")).lstrip("0")
        for t in self.tickers:
            if cik and str(t.get("cik", "")).lstrip("0") == cik:
                hits.add(t["ticker"])
        haystack = " ".join([env.text, *env.entities.get("organizations", [])])
        for ticker, rx in self._name_re:
            if rx.search(haystack):
                hits.add(ticker)
        return sorted(hits)

    def handle_match(self, env: Envelope) -> bool:
        return env.entities.get("did") in self.dids


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)




def http_get(url: str, user_agent: str, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return resp.read()


def http_post_json(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass
    try:  # GDELT seendate: 20261114T153000Z
        return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None



EDGAR_TITLE = re.compile(r"^(?P<form>\S+) - (?P<company>.+?) \((?P<cik>\d{10})\)")
EDGAR_ITEMS = re.compile(r"Item (\d+\.\d+)")
EDGAR_ACCESSION = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})")


def parse_edgar_atom(body: bytes) -> list[Envelope]:
    """SEC EDGAR 'current events' Atom feed. id is the accession number, entities carry CIK and items."""
    out = []
    for entry in ET.fromstring(body).iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", entry.findtext(f"{ATOM}summary") or "")
        summary = re.sub(r"\s+", " ", summary).strip()
        link = entry.find(f"{ATOM}link")
        url = link.get("href", "") if link is not None else ""
        raw_id = entry.findtext(f"{ATOM}id") or url
        acc = EDGAR_ACCESSION.search(raw_id)
        m = EDGAR_TITLE.match(title)
        entities = {"form": m["form"], "company": m["company"], "cik": m["cik"]} if m else {}
        entities["items"] = sorted(set(EDGAR_ITEMS.findall(summary)))
        out.append(Envelope(
            source="edgar", id=acc[1] if acc else raw_id, ts=parse_time(entry.findtext(f"{ATOM}updated")),
            url=url, text=f"{title}. {summary}".strip(), entities=entities,
        ))
    return out


def parse_rss(body: bytes, feed_name: str) -> list[Envelope]:
    """RSS 2.0 or Atom. id is the guid (or link), so a re-published item dedups."""
    root = ET.fromstring(body)
    out = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item.findtext("description") or "")).strip()
        out.append(Envelope(
            source="rss", id=(item.findtext("guid") or link).strip(), ts=parse_time(item.findtext("pubDate")),
            url=link, text=f"{title}. {desc}" if desc else title, entities={"feed": feed_name},
        ))
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        url = link.get("href", "") if link is not None else ""
        out.append(Envelope(
            source="rss", id=(entry.findtext(f"{ATOM}id") or url).strip(),
            ts=parse_time(entry.findtext(f"{ATOM}updated")), url=url,
            text=(entry.findtext(f"{ATOM}title") or "").strip(), entities={"feed": feed_name},
        ))
    return out


GKG_COLUMNS = 27  # GDELT GKG 2.1: 4 DocumentIdentifier, 3 SourceCommonName, 13 Organizations, 15 Tone, 26 Extras
GKG_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)


def gdelt_latest_gkg(lastupdate: bytes) -> str:
    """The newest GKG batch URL from lastupdate.txt (one line per file: size, md5, url)."""
    for line in lastupdate.decode().splitlines():
        if line.endswith(".gkg.csv.zip"):
            return line.split()[2]
    raise ValueError("lastupdate.txt names no gkg file")


GDELT_BATCH = dt.timedelta(minutes=15)
# lastupdate.txt names a batch before its file is served: the listed gkg answered 404 for 15+ minutes
# while the batches before it were served (2026-11-17). The poll reads the listed batch and the 7
# before it, and fails when the newest batch read is older than 2 hours (a stalled feed).
GDELT_LAG_BATCHES = 8
GDELT_MAX_AGE = GDELT_LAG_BATCHES * GDELT_BATCH


def gdelt_batch_time(url: str) -> dt.datetime:
    """A GKG batch URL's timestamp: .../20261117211500.gkg.csv.zip."""
    name = url.rsplit("/", 1)[-1]
    try:
        return dt.datetime.strptime(name[:14], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise ValueError(f"gkg batch {url!r} has no timestamp") from None


def gdelt_unread(latest: str, cursor: str | None) -> list[str]:
    """Batch URLs after the cursor up to the listed one, oldest first, at most GDELT_LAG_BATCHES."""
    head, name = latest.rsplit("/", 1)
    stamp = gdelt_batch_time(latest)
    after = gdelt_batch_time(cursor) if cursor else None
    urls = [f"{head}/{stamp - back * GDELT_BATCH:%Y%m%d%H%M%S}{name[14:]}" for back in range(GDELT_LAG_BATCHES)]
    return [u for u in reversed(urls) if after is None or gdelt_batch_time(u) > after]


def gdelt_check_fresh(cursor: str | None, now: dt.datetime) -> None:
    """Fail the poll when no batch was read in GDELT_MAX_AGE: GDELT stalled, or its files never appeared."""
    if cursor is None or now - gdelt_batch_time(cursor) > GDELT_MAX_AGE:
        raise LookupError(f"newest gkg batch read is {cursor}, older than {GDELT_MAX_AGE}")


def parse_gdelt_gkg(zipped: bytes) -> list[Envelope]:
    """One 15-minute GKG 2.1 batch. id is the article URL, so a re-crawled article dedups."""
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        rows = z.read(z.namelist()[0]).decode("utf-8", "replace").splitlines()
    out = []
    for row in rows:
        f = row.split("\t")
        if len(f) < GKG_COLUMNS or not f[4].startswith("http"):
            continue
        title = GKG_TITLE.search(f[26])
        tone = f[15].split(",")[0]
        out.append(Envelope(
            source="gdelt", id=f[4], ts=parse_time(f[1][:8] + "T" + f[1][8:14] + "Z"), url=f[4],
            text=title[1].strip() if title else "",
            entities={"domain": f[3], "organizations": [o for o in f[13].split(";") if o][:30],
                      "tone": float(tone) if tone else None},
        ))
    return out


def parse_jetstream(msg: dict) -> Envelope | None:
    """One Jetstream event. Only post creates become envelopes; id is the at:// URI."""
    commit = msg.get("commit") or {}
    if msg.get("kind") != "commit" or commit.get("operation") != "create":
        return None
    if commit.get("collection") != "app.bsky.feed.post":
        return None
    record = commit.get("record") or {}
    did, rkey = msg.get("did", ""), commit.get("rkey", "")
    return Envelope(
        source="bluesky", id=f"at://{did}/app.bsky.feed.post/{rkey}", ts=parse_time(record.get("createdAt")),
        url=f"https://bsky.app/profile/{did}/post/{rkey}", text=record.get("text", ""),
        entities={"did": did, "langs": record.get("langs", [])},
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_items (
    source      text        NOT NULL,
    id          text        NOT NULL,
    ts          timestamptz,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    url         text        NOT NULL,
    text        text        NOT NULL,
    entities    jsonb       NOT NULL DEFAULT '{}',
    tickers     text[]      NOT NULL DEFAULT '{}',
    tags        jsonb,
    topic       text        NOT NULL,
    PRIMARY KEY (source, id)
);
CREATE INDEX IF NOT EXISTS feed_items_ingested_at ON feed_items (ingested_at);
CREATE INDEX IF NOT EXISTS feed_items_tickers ON feed_items USING gin (tickers);
CREATE TABLE IF NOT EXISTS feed_cursors (
    source     text PRIMARY KEY,
    cursor     text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


class PgStore:
    """The ledger. The ingester is its only writer; feed_reader (trader) only SELECTs."""

    def __init__(self, dsn: str):
        import psycopg  # installed at pod start; tests use their own store
        from psycopg.types.json import Jsonb

        self._jsonb = Jsonb
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(SCHEMA)

    def insert_new(self, env: Envelope, tickers: list[str]) -> dt.datetime | None:
        """Insert unless (source, id) exists. Returns ingested_at for a new row, None for a duplicate."""
        with self._lock:
            row = self._conn.execute(
                "INSERT INTO feed_items (source, id, ts, url, text, entities, tickers, topic)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (source, id) DO NOTHING RETURNING ingested_at",
                (env.source, env.id, env.ts, env.url, env.text, self._jsonb(env.entities), tickers, env.topic),
            ).fetchone()
        return row[0] if row else None

    def set_tags(self, env: Envelope) -> None:
        with self._lock:
            self._conn.execute("UPDATE feed_items SET tags = %s WHERE source = %s AND id = %s",
                               (self._jsonb(env.tags), env.source, env.id))

    def get_cursor(self, source: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT cursor FROM feed_cursors WHERE source = %s", (source,)).fetchone()
        return row[0] if row else None

    def set_cursor(self, source: str, cursor: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO feed_cursors (source, cursor) VALUES (%s, %s)"
                " ON CONFLICT (source) DO UPDATE SET cursor = EXCLUDED.cursor, updated_at = now()",
                (source, cursor))



TAG_PROMPT = (
    "You tag one news or social item for a market-event study. Reply with JSON only, keys: "
    '"tickers" (US tickers the item is about, [] if none), "entities" (companies, people, '
    'agencies named), "event_type" (one of: earnings, guidance, management, m_and_a, legal, '
    "regulatory, product, macro, analyst, filing, other), \"sentiment\" (-1.0 to 1.0 for the "
    "named companies; 0 if neutral or unclear)."
)
EVENT_TYPES = {"earnings", "guidance", "management", "m_and_a", "legal", "regulatory", "product",
               "macro", "analyst", "filing", "other"}


def parse_tags(content: str) -> dict:
    """Validate the model's JSON, so a malformed reply cannot reach the ledger as a tag."""
    m = re.search(r"\{.*\}", content, re.S)
    raw = json.loads(m[0] if m else content)
    event = str(raw.get("event_type", "other")).lower()
    sentiment = max(-1.0, min(1.0, float(raw.get("sentiment", 0.0))))
    return {
        "tickers": sorted({str(t).upper().lstrip("$") for t in raw.get("tickers", []) if str(t).strip()}),
        "entities": [str(e) for e in raw.get("entities", [])][:20],
        "event_type": event if event in EVENT_TYPES else "other",
        "sentiment": round(sentiment, 3),
    }


class Tagger:
    def __init__(self, url: str, model: str, api_key: str):
        self.url, self.model, self.api_key = url, model, api_key

    def tag(self, env: Envelope) -> dict:
        resp = http_post_json(
            self.url,
            {"model": self.model, "temperature": 0, "max_tokens": 1000,  # fast is a reasoning model; 300 ran out before the answer on long filings
             "messages": [{"role": "system", "content": TAG_PROMPT},
                          {"role": "user", "content": f"source: {env.source}\n{env.text[:4000]}"}]},
            {"Authorization": f"Bearer {self.api_key}"},
        )
        content = resp["choices"][0]["message"].get("content")
        if not content:
            raise ValueError(f"empty reply (finish_reason {resp['choices'][0].get('finish_reason')})")
        return parse_tags(content)




def topic_for(env: Envelope, tickers: list[str]) -> str:
    """feeds/<source>/<topic>: the first watchlist ticker, else 'all'."""
    return tickers[0].lower() if tickers else "all"


def route_matches(match: dict, env: Envelope, tickers: list[str]) -> bool:
    if "sources" in match and env.source not in match["sources"]:
        return False
    if match.get("watchlist") and not tickers:
        return False
    tags = env.tags or {}
    if "event_types" in match and tags.get("event_type") not in match["event_types"]:
        return False
    if "min_abs_sentiment" in match and abs(tags.get("sentiment", 0.0)) < match["min_abs_sentiment"]:
        return False
    return True


class Publisher:
    """MQTT (QoS 1), Home Assistant discovery, ntfy. One paho client; it reconnects on its own."""

    def __init__(self, cfg: dict, client=None):
        self.cfg = cfg
        self.prefix = cfg["mqtt"].get("topic_prefix", "feeds")
        if client is None:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="feed-ingest")
            client.connect_async(cfg["mqtt"]["host"], int(cfg["mqtt"].get("port", 1883)))
            client.loop_start()
        self.client = client
        self.ha_announced: set[str] = set()

    def publish(self, env: Envelope, tickers: list[str], ingested_at: dt.datetime) -> list[str]:
        """Publish the item; returns the names of the routes it went to (mqtt first)."""
        payload = env.payload(ingested_at)
        payload["tickers"] = tickers
        self.client.publish(f"{self.prefix}/{env.source}/{env.topic}", json.dumps(payload), qos=1)
        sent = ["mqtt"]
        for route in self.cfg.get("routes", []):
            if not route_matches(route.get("match", {}), env, tickers):
                continue
            try:
                if route["kind"] == "ha":
                    self._ha(route, env, tickers, payload)
                elif route["kind"] == "ntfy":
                    self._ntfy(route, env, tickers)
                else:
                    raise ValueError(f"unknown route kind {route['kind']!r}")
                sent.append(route["name"])
                METRICS.inc("feed_ingest_routed_total", route=route["name"])
            except Exception as exc:  # one consumer failing must not stop the others
                log.warning("route %s failed for %s/%s: %s", route["name"], env.source, env.id, exc)
                METRICS.inc("feed_ingest_route_errors_total", route=route["name"])
        return sent

    def _ha(self, route: dict, env: Envelope, tickers: list[str], payload: dict) -> None:
        """One HA sensor per source: state = the newest headline, attributes = the whole item."""
        prefix = route.get("discovery_prefix", "homeassistant")
        state_topic = f"{self.prefix}/{env.source}/latest"
        if env.source not in self.ha_announced:
            config = {
                "name": f"Feed {env.source} latest",
                "unique_id": f"feed_ingest_{env.source}_latest",
                "object_id": f"feed_ingest_{env.source}_latest",
                "state_topic": state_topic,
                "value_template": "{{ value_json.text[:250] }}",
                "json_attributes_topic": state_topic,
                "icon": "mdi:newspaper-variant-outline",
                "device": {"identifiers": ["feed_ingest"], "name": "feed-ingest", "model": "home-config apps/feed-ingest"},
            }
            self.client.publish(f"{prefix}/sensor/feed_ingest_{env.source}/config", json.dumps(config), qos=1, retain=True)
            self.ha_announced.add(env.source)
        self.client.publish(state_topic, json.dumps(payload), qos=1, retain=True)

    def _ntfy(self, route: dict, env: Envelope, tickers: list[str]) -> None:
        tags = env.tags or {}
        title = f"{' '.join(tickers) or env.source}: {tags.get('event_type', 'item')} ({env.source})"
        req = urllib.request.Request(
            route["url"], data=env.text[:1000].encode(), method="POST",
            headers={"Title": title[:200], "Priority": str(route.get("priority", 2)),
                     "Tags": "newspaper", "Click": env.url},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass




class Metrics:
    """Counters and gauges with labels, rendered in Prometheus text format."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[tuple, float] = {}
        self.gauges: dict[tuple, float] = {}

    @staticmethod
    def _key(name, labels):
        return (name, tuple(sorted(labels.items())))

    def inc(self, name, value=1.0, **labels):
        with self._lock:
            k = self._key(name, labels)
            self.counters[k] = self.counters.get(k, 0.0) + value

    def set(self, name, value, **labels):
        with self._lock:
            self.gauges[self._key(name, labels)] = float(value)

    def render(self) -> str:
        lines = []
        with self._lock:
            for kind, series in (("counter", self.counters), ("gauge", self.gauges)):
                for name in sorted({k[0] for k in series}):
                    lines.append(f"# TYPE {name} {kind}")
                    for (n, labels), v in sorted(series.items()):
                        if n == name:
                            lab = ",".join(f'{k}="{val}"' for k, val in labels)
                            lines.append(f"{name}{{{lab}}} {v}" if lab else f"{name} {v}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()


class MetricsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path not in ("/metrics", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        body = (METRICS.render() if self.path == "/metrics" else "ok").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)




class Pipeline:
    def __init__(self, cfg: dict, store, publisher, tagger, watchlist: Watchlist):
        self.cfg, self.store, self.publisher, self.tagger, self.watchlist = cfg, store, publisher, tagger, watchlist
        self.keep = {name: s.get("keep", "all") for name, s in cfg["sources"].items()}
        self.tag_sources = {name for name, s in cfg["sources"].items() if s.get("tag", True)}

    def handle(self, env: Envelope) -> str:
        """Run one envelope through the pipeline. Returns its outcome: dropped, duplicate or published."""
        tickers = self.watchlist.match(env)
        keep = self.keep.get(env.source, "all")
        if keep == "watchlist" and not tickers and not self.watchlist.handle_match(env):
            METRICS.inc("feed_ingest_items_total", source=env.source, outcome="dropped")
            return "dropped"
        env.topic = topic_for(env, tickers)
        ingested_at = self.store.insert_new(env, tickers)
        if ingested_at is None:
            METRICS.inc("feed_ingest_items_total", source=env.source, outcome="duplicate")
            return "duplicate"
        if self.tagger is not None and env.source in self.tag_sources:
            try:
                env.tags = self.tagger.tag(env)
                self.store.set_tags(env)
            except Exception as exc:  # untagged is visible (tags null), never a silent default
                log.warning("tag failed for %s/%s: %s", env.source, env.id, exc)
                METRICS.inc("feed_ingest_tag_errors_total", source=env.source)
        self.publisher.publish(env, tickers, ingested_at)
        METRICS.inc("feed_ingest_items_total", source=env.source, outcome="published")
        METRICS.set("feed_ingest_last_item_timestamp_seconds", ingested_at.timestamp(), source=env.source)
        return "published"




def mark_ok(source: str) -> None:
    METRICS.set("feed_ingest_last_success_timestamp_seconds", time.time(), source=source)


def poll_loop(name: str, scfg: dict, cfg: dict, store, out: queue.Queue, stop: threading.Event) -> None:
    """One polled source, on the cadence its terms allow (config `interval_s`)."""
    ua = cfg["user_agent"]
    interval = int(scfg["interval_s"])
    METRICS.set("feed_ingest_cadence_seconds", interval, source=name)
    while not stop.is_set():
        try:
            if name == "edgar":
                items = parse_edgar_atom(http_get(scfg["url"], ua, "application/atom+xml"))
            elif name == "gdelt":
                # Each served batch after the cursor is read once, oldest first, one at a time (the pod
                # has 256Mi). One not served yet is retried next poll; older than the window is skipped.
                latest = gdelt_latest_gkg(http_get(scfg["url"], ua))
                for batch in gdelt_unread(latest, store.get_cursor("gdelt")):
                    try:
                        zipped = http_get(batch, ua)
                    except urllib.error.HTTPError as exc:
                        if exc.code != 404:
                            raise
                        continue
                    for env in reversed(parse_gdelt_gkg(zipped)):
                        out.put(env)
                    store.set_cursor("gdelt", batch)
                gdelt_check_fresh(store.get_cursor("gdelt"), dt.datetime.now(dt.timezone.utc))
                items = []
            elif name == "rss":
                items = []
                for feed in scfg["feeds"]:
                    items += parse_rss(http_get(feed["url"], ua), feed["name"])
            else:
                raise ValueError(f"no poller for source {name!r}")
            for env in reversed(items):  # oldest first, so the ledger order follows publication order
                out.put(env)
            mark_ok(name)
        except Exception as exc:
            log.warning("poll %s failed: %s", name, exc)
            METRICS.inc("feed_ingest_poll_errors_total", source=name)
        stop.wait(interval)


def resolve_dids(handles: list[str], resolver: str, ua: str) -> set[str]:
    dids = set()
    for h in handles:
        try:
            body = http_get(f"{resolver}?{urllib.parse.urlencode({'handle': h})}", ua, "application/json")
            dids.add(json.loads(body)["did"])
        except Exception as exc:
            log.warning("bluesky: cannot resolve %s: %s", h, exc)
    return dids


def jetstream_loop(scfg: dict, cfg: dict, store, out: queue.Queue, stop: threading.Event) -> None:
    """Bluesky Jetstream posts. Resumes from the stored cursor (time_us) after a restart."""
    from websockets.sync.client import connect

    METRICS.set("feed_ingest_cadence_seconds", int(scfg["interval_s"]), source="bluesky")
    backoff = 1
    while not stop.is_set():
        cursor = store.get_cursor("bluesky")
        params = {"wantedCollections": "app.bsky.feed.post"}
        if cursor:
            params["cursor"] = cursor
        url = f"{scfg['url']}?{urllib.parse.urlencode(params)}"
        try:
            with connect(url, open_timeout=HTTP_TIMEOUT_S, close_timeout=5, max_size=2**20) as ws:
                backoff, last_saved = 1, time.monotonic()
                while not stop.is_set():
                    msg = json.loads(ws.recv(timeout=60))
                    mark_ok("bluesky")
                    env = parse_jetstream(msg)
                    if env is not None:
                        out.put(env)
                    if time.monotonic() - last_saved > 30 and msg.get("time_us"):
                        store.set_cursor("bluesky", str(msg["time_us"]))
                        last_saved = time.monotonic()
        except Exception as exc:
            log.warning("jetstream: %s; reconnect in %ss", exc, backoff)
            METRICS.inc("feed_ingest_poll_errors_total", source="bluesky")
            stop.wait(backoff)
            backoff = min(backoff * 2, 300)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config(os.environ.get("FEED_CONFIG", "/app/config.toml"))
    watchlist = Watchlist(cfg["watchlist"])
    store = PgStore(os.environ["DATABASE_URL"])
    tagger = Tagger(cfg["tagger"]["url"], cfg["tagger"]["model"], os.environ["LITELLM_API_KEY"]) \
        if cfg["tagger"].get("enabled", True) else None
    publisher = Publisher(cfg)
    pipeline = Pipeline(cfg, store, publisher, tagger, watchlist)

    port = int(cfg.get("metrics_port", 9100))
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), MetricsHandler).serve_forever, daemon=True).start()

    items: queue.Queue = queue.Queue(maxsize=10000)
    stop = threading.Event()
    for name, scfg in cfg["sources"].items():
        if not scfg.get("enabled", True):
            continue
        if name == "bluesky":
            watchlist.dids = resolve_dids(watchlist.handles, scfg["resolver"], cfg["user_agent"])
            target, args = jetstream_loop, (scfg, cfg, store, items, stop)
        else:
            target, args = poll_loop, (name, scfg, cfg, store, items, stop)
        threading.Thread(target=target, args=args, name=name, daemon=True).start()
        log.info("source %s started (keep=%s)", name, scfg.get("keep", "all"))

    failures = 0
    while True:
        env = items.get()
        METRICS.set("feed_ingest_queue_depth", items.qsize())
        try:
            pipeline.handle(env)
            failures = 0
        except Exception as exc:
            log.error("pipeline failed for %s/%s: %s", env.source, env.id, exc)
            METRICS.inc("feed_ingest_pipeline_errors_total", source=env.source)
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:  # a dead connection never heals; the restart reconnects
                raise SystemExit(f"{failures} pipeline failures in a row")


if __name__ == "__main__":
    main()
