#!/usr/bin/env python3
"""spotify-concerts — a tiny, dependency-free bridge service.

Publishes an iCalendar (.ics) feed of upcoming concerts near home for the
artists you actually listen to on Spotify. Home Assistant's Remote Calendar
subscribes to it, so concerts land on the dashboard automatically — nothing to
mark by hand.

Design goals (also the template for future "bridge" services):
  * PURE STDLIB — runs on a stock python image, no pip install, nothing to rot.
  * FAIL SOFT — any upstream hiccup yields a valid (possibly empty) calendar,
    never a 500, so HA never sees a broken feed.
  * PLUGGABLE ARTIST SOURCE — Spotify (top + followed) when creds are present,
    else a static SPOTIFY_ARTISTS_OVERRIDE list (also a no-Spotify fallback).
  * CACHED — upstream is polled at most once per CACHE_TTL; HA can poll freely.

Endpoints:  GET /concerts.ics   GET /healthz   GET /
"""
import base64
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── config (env) ───────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY", "").strip()
ARTISTS_OVERRIDE = os.environ.get("SPOTIFY_ARTISTS_OVERRIDE", "").strip()

HOME_LAT = float(os.environ.get("HOME_LAT", "32.72"))      # SD city centroid (public)
HOME_LON = float(os.environ.get("HOME_LON", "-117.16"))
RADIUS_MI = int(float(os.environ.get("RADIUS_MI", "75")))
MAX_ARTISTS = int(os.environ.get("MAX_ARTISTS", "60"))
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "43200"))  # 12h
PORT = int(os.environ.get("PORT", "8080"))

UA = {"User-Agent": "spotify-concerts/1.0 (+homelab HA bridge)"}


# ── tiny HTTP helper ───────────────────────────────────────────────────────
def _fetch(url, headers=None, data=None, timeout=25):
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ── Spotify: artists you listen to ─────────────────────────────────────────
def spotify_access_token():
    """Refresh-token (user) flow → short-lived access token."""
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET and SPOTIFY_REFRESH_TOKEN):
        raise RuntimeError("spotify creds not set")
    basic = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": SPOTIFY_REFRESH_TOKEN}
    ).encode()
    tok = _fetch(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    return tok["access_token"]


def spotify_artists():
    """Deduped artist names from your top artists + followed artists."""
    token = spotify_access_token()
    auth = {"Authorization": f"Bearer {token}"}
    names, seen = [], set()

    def add(items):
        for a in items:
            n = (a or {}).get("name", "").strip()
            k = n.lower()
            if n and k not in seen:
                seen.add(k)
                names.append(n)

    for term in ("medium_term", "long_term"):
        try:
            d = _fetch(
                f"https://api.spotify.com/v1/me/top/artists?limit=50&time_range={term}",
                headers=auth,
            )
            add(d.get("items", []))
        except Exception as e:  # noqa: BLE001
            print(f"[spotify] top/{term} failed: {e}", flush=True)
    try:
        after = None
        for _ in range(4):  # up to ~200 followed
            url = "https://api.spotify.com/v1/me/following?type=artist&limit=50"
            if after:
                url += f"&after={after}"
            d = _fetch(url, headers=auth).get("artists", {})
            add(d.get("items", []))
            after = (d.get("cursors") or {}).get("after")
            if not after:
                break
    except Exception as e:  # noqa: BLE001
        print(f"[spotify] following failed: {e}", flush=True)
    return names


# ── Ticketmaster: concerts near home ───────────────────────────────────────
def haversine_mi(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def ticketmaster_events(artist):
    """Upcoming music events near home whose lineup actually matches `artist`."""
    if not TICKETMASTER_API_KEY:
        raise RuntimeError("ticketmaster key not set")
    q = urllib.parse.urlencode({
        "apikey": TICKETMASTER_API_KEY,
        "keyword": artist,
        "classificationName": "music",
        "latlong": f"{HOME_LAT},{HOME_LON}",
        "radius": RADIUS_MI,
        "unit": "miles",
        "sort": "date,asc",
        "size": 20,
    })
    d = _fetch(f"https://app.ticketmaster.com/discovery/v2/events.json?{q}")
    out = []
    for ev in (d.get("_embedded", {}) or {}).get("events", []):
        # keep only events whose attractions include this artist (keyword search
        # is fuzzy) — guards against "artist name" matching a venue/other act.
        attractions = (ev.get("_embedded", {}) or {}).get("attractions", []) or []
        names = {(a.get("name") or "").lower() for a in attractions}
        if artist.lower() not in names:
            continue
        out.append(_normalize_tm(ev, artist))
    return [e for e in out if e]


def _normalize_tm(ev, artist):
    dates = ev.get("dates", {}) or {}
    start = dates.get("start", {}) or {}
    dt = start.get("dateTime")  # e.g. 2026-08-01T02:00:00Z (UTC)
    local_date = start.get("localDate")
    local_time = start.get("localTime")
    venues = (ev.get("_embedded", {}) or {}).get("venues", []) or [{}]
    v = venues[0] if venues else {}
    loc = v.get("location", {}) or {}
    try:
        vlat, vlon = float(loc.get("latitude")), float(loc.get("longitude"))
    except (TypeError, ValueError):
        vlat = vlon = None
    return {
        "id": ev.get("id"),
        "artist": artist,
        "title": ev.get("name") or artist,
        "url": ev.get("url", ""),
        "dt_utc": dt,               # ISO Z, may be None
        "local_date": local_date,   # YYYY-MM-DD
        "local_time": local_time,   # HH:MM:SS or None
        "venue": v.get("name", ""),
        "city": ((v.get("city") or {}).get("name")) or "",
        "state": ((v.get("state") or {}).get("stateCode")) or "",
        "lat": vlat,
        "lon": vlon,
    }


# ── iCalendar builder (hand-rolled; concerts are simple events) ─────────────
def _ics_dt_utc(iso_z):
    # "2026-08-01T02:00:00Z" -> "20260801T020000Z"
    try:
        d = datetime.fromisoformat(iso_z.replace("Z", "+00:00")).astimezone(timezone.utc)
        return d.strftime("%Y%m%dT%H%M%SZ"), True
    except Exception:  # noqa: BLE001
        return None, False


def _ics_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    # RFC5545 75-octet folding
    out, cur = [], line
    while len(cur.encode()) > 73:
        cut = 73
        while len(cur[:cut].encode()) > 73:
            cut -= 1
        out.append(cur[:cut])
        cur = " " + cur[cut:]
    out.append(cur)
    return "\r\n".join(out)


def build_ics(events, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//homelab//spotify-concerts//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Concerts (Spotify)",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for e in events:
        uid = f"{e.get('id') or _ics_escape(e['artist'])}@spotify-concerts"
        dtutc, timed = _ics_dt_utc(e.get("dt_utc"))
        ev = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
        if timed:
            ev.append(f"DTSTART:{dtutc}")
        elif e.get("local_date"):
            ev.append(f"DTSTART;VALUE=DATE:{e['local_date'].replace('-', '')}")
        else:
            continue  # no usable date → skip
        where = ", ".join(x for x in [e.get("venue"), e.get("city"), e.get("state")] if x)
        ev.append(_fold(f"SUMMARY:🎵 {_ics_escape(e['artist'])} — {_ics_escape(e.get('venue') or e.get('city'))}"))
        if where:
            ev.append(_fold(f"LOCATION:{_ics_escape(where)}"))
        desc = f"{e.get('title','')}".strip()
        if e.get("url"):
            desc = (desc + f"\\nTickets: {e['url']}").strip()
        if desc:
            ev.append(_fold(f"DESCRIPTION:{_ics_escape(desc)}"))
        if e.get("url"):
            ev.append(_fold(f"URL:{e['url']}"))
        ev.append("END:VEVENT")
        lines += ev
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# ── refresh + cache ────────────────────────────────────────────────────────
_cache = {"ts": 0.0, "ics": None, "count": 0, "artists": 0, "error": None}
_lock = threading.Lock()


def _artists():
    if ARTISTS_OVERRIDE:
        return [a.strip() for a in ARTISTS_OVERRIDE.split(",") if a.strip()]
    return spotify_artists()


def refresh():
    err = None
    events, artists = [], []
    try:
        artists = _artists()[:MAX_ARTISTS]
    except Exception as e:  # noqa: BLE001
        err = f"artists: {e}"
        print(f"[refresh] {err}", flush=True)
    seen = set()
    for a in artists:
        try:
            for ev in ticketmaster_events(a):
                # distance guard when coords present (TM radius already filters,
                # this just drops the odd mis-geocoded result)
                if ev.get("lat") is not None and haversine_mi(
                    HOME_LAT, HOME_LON, ev["lat"], ev["lon"]) > RADIUS_MI + 15:
                    continue
                if ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                events.append(ev)
            time.sleep(0.25)  # be polite to TM (5 req/s cap)
        except Exception as e:  # noqa: BLE001
            print(f"[refresh] ticketmaster '{a}': {e}", flush=True)
    events.sort(key=lambda e: (e.get("dt_utc") or e.get("local_date") or "9999"))
    ics = build_ics(events)
    with _lock:
        _cache.update(ts=time.time(), ics=ics, count=len(events),
                      artists=len(artists), error=err)
    print(f"[refresh] artists={len(artists)} events={len(events)} err={err}", flush=True)
    return ics


def get_ics():
    with _lock:
        fresh = _cache["ics"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL
        cached = _cache["ics"]
    if fresh:
        return cached
    try:
        return refresh()
    except Exception as e:  # noqa: BLE001
        print(f"[get_ics] refresh crashed: {e}", flush=True)
        return cached or build_ics([])  # always valid


# ── HTTP server ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/concerts.ics", "/calendar.ics"):
            self._send(200, get_ics(), "text/calendar; charset=utf-8")
        elif path == "/healthz":
            self._send(200, "ok")
        elif path == "/":
            with _lock:
                meta = {k: _cache[k] for k in ("ts", "count", "artists", "error")}
            meta["spotify"] = bool(SPOTIFY_REFRESH_TOKEN) or f"override:{bool(ARTISTS_OVERRIDE)}"
            meta["ticketmaster"] = bool(TICKETMASTER_API_KEY)
            self._send(200, json.dumps(meta, indent=2), "application/json")
        else:
            self._send(404, "not found")

    def log_message(self, *a):  # quieter logs
        return


def main():
    print(f"spotify-concerts listening on :{PORT} "
          f"(spotify={bool(SPOTIFY_REFRESH_TOKEN)}, tm={bool(TICKETMASTER_API_KEY)}, "
          f"override={bool(ARTISTS_OVERRIDE)})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
