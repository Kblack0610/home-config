"""Offline tests for feed-ingest: stdlib only, no network, no broker.

The pipeline runs against an in-memory store with the ledger's dedup contract and a recording MQTT
client. FEED_TEST_DSN=postgresql://... also runs the same contract against real Postgres (needs
psycopg); CI runs without it.
"""

import datetime as dt
import io
import json
import os
import pathlib
import sys
import tomllib
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ingest  # noqa: E402

HERE = pathlib.Path(__file__).parent

EDGAR_FIXTURE = b"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - NVIDIA CORP (0001045810) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/0001045810-26-000123-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-11-12 &lt;b&gt;AccNo:&lt;/b&gt; 0001045810-26-000123 &lt;b&gt;Size:&lt;/b&gt; 200 KB&lt;br&gt;Item 5.02: Departure of Directors or Certain Officers&lt;br&gt;Item 9.01: Financial Statements and Exhibits</summary>
<updated>2026-11-12T16:31:07-05:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0001045810-26-000123</id>
</entry>
<entry>
<title>8-K - ACME WIDGETS INC (0009999999) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/9999999/x-index.htm"/>
<summary type="html">Item 8.01: Other Events</summary>
<updated>2026-11-12T16:30:00-05:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0009999999-26-000001</id>
</entry>
</feed>
"""

RSS_FIXTURE = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Nvidia board ousts Huang</title><link>https://example.com/a</link>
<guid>https://example.com/a</guid><description>&lt;p&gt;The board voted.&lt;/p&gt;</description>
<pubDate>Thu, 12 Nov 2026 21:40:00 GMT</pubDate></item></channel></rss>"""


def gkg_zip(rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20261112214500.gkg.csv", "\n".join("\t".join(r) for r in rows))
    return buf.getvalue()


def gkg_row(url: str, orgs: str, title: str) -> list[str]:
    row = [""] * ingest.GKG_COLUMNS
    row[0], row[1], row[3], row[4] = "20261112214500-1", "20261112214500", "example.com", url
    row[13], row[15] = orgs, "-4.5,1.2,5.7"
    row[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    return row


class MemoryStore:
    """The PgStore contract: insert_new returns ingested_at once per (source, id), then None."""

    def __init__(self):
        self.rows, self.cursors = {}, {}

    def insert_new(self, env, tickers):
        key = (env.source, env.id)
        if key in self.rows:
            return None
        self.rows[key] = {"env": env, "tickers": tickers, "tags": None}
        return dt.datetime(2026, 11, 12, 21, 45, tzinfo=dt.timezone.utc)

    def set_tags(self, env):
        self.rows[(env.source, env.id)]["tags"] = env.tags

    def get_cursor(self, source):
        return self.cursors.get(source)

    def set_cursor(self, source, cursor):
        self.cursors[source] = cursor


class RecordingClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, json.loads(payload), qos, retain))


class FakeTagger:
    def __init__(self, tags=None, fail=False):
        self.tags, self.fail, self.calls = tags, fail, 0

    def tag(self, env):
        self.calls += 1
        if self.fail:
            raise TimeoutError("gateway down")
        return dict(self.tags)


def config(routes=None):
    cfg = tomllib.loads((HERE / "config.toml").read_text())
    cfg["routes"] = routes if routes is not None else []
    return cfg


def pipeline(cfg=None, tagger=None):
    cfg = cfg or config()
    client, store = RecordingClient(), MemoryStore()
    p = ingest.Pipeline(cfg, store, ingest.Publisher(cfg, client=client), tagger,
                        ingest.Watchlist(cfg["watchlist"]))
    return p, store, client


class EdgarThroughPipeline(unittest.TestCase):
    def test_fixture_item_is_enveloped_deduped_and_published(self):
        tags = {"tickers": ["NVDA"], "entities": ["NVIDIA"], "event_type": "management", "sentiment": -0.7}
        p, store, client = pipeline(tagger=FakeTagger(tags))
        env = ingest.parse_edgar_atom(EDGAR_FIXTURE)[0]
        self.assertEqual(env.id, "0001045810-26-000123")
        self.assertEqual(env.entities["cik"], "0001045810")
        self.assertEqual(env.entities["items"], ["5.02", "9.01"])
        self.assertEqual(p.handle(env), "published")
        topic, payload, qos, _ = client.sent[0]
        self.assertEqual(topic, "feeds/edgar/nvda")
        self.assertEqual(qos, 1)
        self.assertEqual(payload["tickers"], ["NVDA"])
        self.assertEqual(payload["tags"]["event_type"], "management")
        self.assertEqual(payload["ingested_at"], "2026-11-12T21:45:00+00:00")
        self.assertEqual(store.rows[("edgar", env.id)]["tags"], tags)

    def test_negative_control_duplicate_publishes_once(self):
        p, _, client = pipeline(tagger=FakeTagger({"event_type": "filing", "sentiment": 0}))
        first, again = ingest.parse_edgar_atom(EDGAR_FIXTURE)[0], ingest.parse_edgar_atom(EDGAR_FIXTURE)[0]
        self.assertEqual(p.handle(first), "published")
        self.assertEqual(p.handle(again), "duplicate")
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(p.tagger.calls, 1, "a duplicate must not spend a model call")

    def test_keep_all_publishes_an_unwatched_filer_on_topic_all(self):
        p, _, client = pipeline()
        self.assertEqual(p.handle(ingest.parse_edgar_atom(EDGAR_FIXTURE)[1]), "published")
        self.assertEqual(client.sent[0][0], "feeds/edgar/all")

    def test_tag_failure_publishes_untagged_and_counts(self):
        p, store, client = pipeline(tagger=FakeTagger(fail=True))
        env = ingest.parse_edgar_atom(EDGAR_FIXTURE)[0]
        self.assertEqual(p.handle(env), "published")
        self.assertIsNone(client.sent[0][1]["tags"])
        self.assertIn("feed_ingest_tag_errors_total", ingest.METRICS.render())


class TierOneRules(unittest.TestCase):
    def test_watchlist_source_drops_unmatched_before_the_ledger(self):
        p, store, client = pipeline()
        env = ingest.Envelope("bluesky", "at://x/1", None, "u", "lunch was good", {"did": "did:plc:x"})
        self.assertEqual(p.handle(env), "dropped")
        self.assertEqual(store.rows, {})
        self.assertEqual(client.sent, [])

    def test_ticker_matches_in_capitals_and_cashtag_name_in_any_case(self):
        wl = ingest.Watchlist({"tickers": [{"ticker": "ARM", "names": ["Arm Holdings"]}]})
        mk = lambda text: ingest.Envelope("rss", "1", None, "u", text)  # noqa: E731
        self.assertEqual(wl.match(mk("$ARM up 4%")), ["ARM"])
        self.assertEqual(wl.match(mk("arm holdings guidance")), ["ARM"])
        self.assertEqual(wl.match(mk("my arm hurts")), [])
        self.assertEqual(wl.match(mk("FARM stocks")), [])

    def test_untagged_source_skips_the_model(self):
        p, _, client = pipeline(tagger=FakeTagger({"event_type": "other", "sentiment": 0}))
        env = ingest.Envelope("bluesky", "at://x/2", None, "u", "$NVDA dumping", {"did": "did:plc:x"})
        self.assertEqual(p.handle(env), "published")
        self.assertEqual((p.tagger.calls, client.sent[0][0]), (0, "feeds/bluesky/nvda"))

    def test_watched_handle_keeps_a_post_without_a_ticker(self):
        p, _, client = pipeline()
        p.watchlist.dids = {"did:plc:watched"}
        env = ingest.Envelope("bluesky", "at://w/1", None, "u", "big news soon", {"did": "did:plc:watched"})
        self.assertEqual(p.handle(env), "published")
        self.assertEqual(client.sent[0][0], "feeds/bluesky/all")


class Routes(unittest.TestCase):
    def test_config_routes_ha_and_ntfy_only_on_their_match(self):
        cfg = config()
        ha = next(r for r in tomllib.loads((HERE / "config.toml").read_text())["routes"] if r["kind"] == "ha")
        ntfy_rule = {"name": "ntfy-test", "kind": "ntfy", "url": "http://x", "match":
                     {"watchlist": True, "event_types": ["management"], "min_abs_sentiment": 0.5}}
        sent_ntfy = []
        cfg["routes"] = [ha, ntfy_rule]
        p, _, client = pipeline(cfg, FakeTagger({"event_type": "management", "sentiment": -0.8}))
        p.publisher._ntfy = lambda route, env, tickers: sent_ntfy.append(env.id)
        p.handle(ingest.parse_edgar_atom(EDGAR_FIXTURE)[0])
        topics = [t for t, *_ in client.sent]
        self.assertIn("homeassistant/sensor/feed_ingest_edgar/config", topics)
        self.assertIn("feeds/edgar/latest", topics)
        self.assertEqual(sent_ntfy, ["0001045810-26-000123"])
        # An unwatched filer goes to MQTT only.
        before = len(client.sent)
        p.handle(ingest.parse_edgar_atom(EDGAR_FIXTURE)[1])
        self.assertEqual([t for t, *_ in client.sent[before:]], ["feeds/edgar/all"])
        self.assertEqual(len(sent_ntfy), 1)

    def test_route_match_rules(self):
        env = ingest.Envelope("gdelt", "1", None, "u", "t", tags={"event_type": "product", "sentiment": 0.2})
        self.assertTrue(ingest.route_matches({}, env, []))
        self.assertFalse(ingest.route_matches({"watchlist": True}, env, []))
        self.assertFalse(ingest.route_matches({"sources": ["edgar"]}, env, ["NVDA"]))
        self.assertFalse(ingest.route_matches({"event_types": ["legal"]}, env, ["NVDA"]))
        self.assertFalse(ingest.route_matches({"min_abs_sentiment": 0.5}, env, ["NVDA"]))

    def test_a_failing_route_does_not_stop_the_others(self):
        cfg = config([{"name": "bad", "kind": "nope", "match": {}}])
        p, _, client = pipeline(cfg)
        p.handle(ingest.parse_edgar_atom(EDGAR_FIXTURE)[1])
        self.assertEqual(len(client.sent), 1)
        self.assertIn('feed_ingest_route_errors_total{route="bad"}', ingest.METRICS.render())


class Parsers(unittest.TestCase):
    def test_rss(self):
        [env] = ingest.parse_rss(RSS_FIXTURE, "test")
        self.assertEqual((env.id, env.text), ("https://example.com/a", "Nvidia board ousts Huang. The board voted."))
        self.assertEqual(env.ts, dt.datetime(2026, 11, 12, 21, 40, tzinfo=dt.timezone.utc))

    def test_gdelt_gkg_batch_and_lastupdate(self):
        lastupdate = (b"1 a http://data.gdeltproject.org/gdeltv2/20261112214500.export.CSV.zip\n"
                      b"2 b http://data.gdeltproject.org/gdeltv2/20261112214500.gkg.csv.zip\n")
        self.assertTrue(ingest.gdelt_latest_gkg(lastupdate).endswith(".gkg.csv.zip"))
        rows = [gkg_row("https://example.com/n", "nvidia;sec", "Huang out at Nvidia"), ["short", "row"]]
        [env] = ingest.parse_gdelt_gkg(gkg_zip(rows))
        self.assertEqual(env.text, "Huang out at Nvidia")
        self.assertEqual(env.entities["tone"], -4.5)
        self.assertEqual(env.ts, dt.datetime(2026, 11, 12, 21, 45, tzinfo=dt.timezone.utc))
        wl = ingest.Watchlist(config()["watchlist"])
        env.text = "Board shake-up"  # an organization alone is a watchlist match
        self.assertEqual(wl.match(env), ["NVDA"])

    def test_jetstream_keeps_post_creates_only(self):
        post = {"did": "did:plc:a", "time_us": 1, "kind": "commit", "commit": {
            "operation": "create", "collection": "app.bsky.feed.post", "rkey": "3k",
            "record": {"text": "$NVDA dumping", "createdAt": "2026-11-12T21:41:00.000Z"}}}
        env = ingest.parse_jetstream(post)
        self.assertEqual(env.id, "at://did:plc:a/app.bsky.feed.post/3k")
        self.assertIsNone(ingest.parse_jetstream({**post, "commit": {**post["commit"], "operation": "delete"}}))
        self.assertIsNone(ingest.parse_jetstream({"kind": "identity", "did": "did:plc:a"}))

    def test_tags_are_validated(self):
        tags = ingest.parse_tags('Sure: {"tickers": ["$nvda"], "event_type": "Coup", "sentiment": -3}')
        self.assertEqual(tags, {"tickers": ["NVDA"], "entities": [], "event_type": "other", "sentiment": -1.0})

    def test_config_parses_and_names_a_contact_user_agent(self):
        cfg = config()
        self.assertRegex(cfg["user_agent"], r"\S+@\S+\.\w+")
        self.assertTrue(all("interval_s" in s and s.get("keep") in ("all", "watchlist") for s in cfg["sources"].values()))


@unittest.skipUnless(os.environ.get("FEED_TEST_DSN"), "FEED_TEST_DSN not set")
class PostgresLedger(unittest.TestCase):
    """The dedup contract MemoryStore fakes, against real Postgres."""

    def test_insert_new_dedups_and_cursor_round_trips(self):
        import psycopg

        dsn = os.environ["FEED_TEST_DSN"]
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS feed_items, feed_cursors")
        store = ingest.PgStore(dsn)
        env = ingest.parse_edgar_atom(EDGAR_FIXTURE)[0]
        self.assertIsNotNone(store.insert_new(env, ["NVDA"]))
        self.assertIsNone(store.insert_new(ingest.parse_edgar_atom(EDGAR_FIXTURE)[0], ["NVDA"]))
        env.tags = {"event_type": "management"}
        store.set_tags(env)
        store.set_cursor("gdelt", "a")
        store.set_cursor("gdelt", "b")
        self.assertEqual(store.get_cursor("gdelt"), "b")
        with psycopg.connect(dsn) as c:
            n, tags, tickers = c.execute("SELECT count(*), max(tags->>'event_type'), max(tickers[1]) FROM feed_items").fetchone()
        self.assertEqual((n, tags, tickers), (1, "management", "NVDA"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
