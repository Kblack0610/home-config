#!/usr/bin/env python3
"""Wall scenery job: keeps the "Wall Scenery" album full of free-licensed scenery.

The wall panels show this album Monday to Saturday (family photos are Sunday
only, see docs/wall-panels.md). Each run picks random files from Wikimedia
Commons featured-picture categories, keeps the wide, large, free-licensed ones,
uploads a screen-sized rendition as the dedicated `wall` Immich user, adds it to
the album, and prunes the oldest uploads past ALBUM_CAP so the set keeps
turning over.

Dedupe key: the upload filename is `wmc-<pageid>.jpg`, so a file already in the
album is never fetched twice. The asset description carries the title, author
and licence, which immich-kiosk shows (show_image_description) as the credit
the CC BY licences require.

Pure stdlib, same as curate.py, so it runs in a stock python:3.12-slim image.
DRY_RUN defaults to true: it logs what it would add and prune and writes nothing.
"""
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich-server").rstrip("/")
API_KEY = os.environ.get("IMMICH_KEY_WALL", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() not in ("false", "0", "no")
ALBUM_NAME = os.environ.get("ALBUM_NAME", "Wall Scenery")
CATEGORIES = [c.strip() for c in os.environ.get(
    "CATEGORIES",
    "Featured pictures of landscapes|Featured pictures of mountains|"
    "Featured pictures of lakes|Featured pictures of waterfalls|"
    "Featured pictures of forests|Featured pictures of astronomy|"
    "Featured pictures of natural phenomena",
).split("|") if c.strip()]
ADD_PER_RUN = int(os.environ.get("ADD_PER_RUN", "40"))
ALBUM_CAP = int(os.environ.get("ALBUM_CAP", "400"))
MIN_WIDTH = int(os.environ.get("MIN_WIDTH", "1920"))
MIN_ASPECT = float(os.environ.get("MIN_ASPECT", "1.3"))
MAX_ASPECT = float(os.environ.get("MAX_ASPECT", "2.1"))
THUMB_WIDTH = int(os.environ.get("THUMB_WIDTH", "1920"))
LICENSE_OK = re.compile(os.environ.get("LICENSE_OK", r"^(CC BY|CC0|Public domain|PD)"), re.I)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks every client for a descriptive User-Agent with a contact.
USER_AGENT = os.environ.get("USER_AGENT", "kblab-wall-scenery/1.0 (home wall panels; hughlio912@gmail.com)")
# Pause between Commons requests: their API rate-limits bursts (seen 2026-11-02).
COMMONS_PAUSE_S = float(os.environ.get("COMMONS_PAUSE_S", "1.0"))
FILENAME_RE = re.compile(r"^wmc-(\d+)\.jpg$")


def log(msg):
    print(msg, flush=True)


def http(req, attempts=4):
    """urlopen with backoff on 429/5xx (retry/backoff, not a sleep-and-hope gate)."""
    for n in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == attempts - 1:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 5 * 2 ** n
            log(f"  {e.code} from {req.full_url[:80]}, retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def immich(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(IMMICH_URL + "/api" + path, data=data, method=method, headers={
        "x-api-key": API_KEY, "Accept": "application/json", "Content-Type": "application/json"})
    raw = http(req)
    return json.loads(raw) if raw else None


def immich_upload(name, blob):
    boundary = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    parts = []
    for k, v in (("fileCreatedAt", now), ("fileModifiedAt", now), ("filename", name)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="assetData"; filename="{name}"\r\n'
                  'Content-Type: image/jpeg\r\n\r\n').encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(IMMICH_URL + "/api/assets", data=b"".join(parts), method="POST", headers={
        "x-api-key": API_KEY, "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(http(req))


def find_or_create_album():
    for a in immich("GET", "/albums"):
        if a["albumName"] == ALBUM_NAME:
            return a["id"]
    if DRY_RUN:
        return None
    return immich("POST", "/albums", {"albumName": ALBUM_NAME})["id"]


def album_assets(album_id):
    """Assets in the album, oldest upload first."""
    out, page = [], 1
    while page:
        res = immich("POST", "/search/metadata", {
            "albumIds": [album_id], "size": 1000, "page": page, "order": "asc"})["assets"]
        out += res["items"]
        page = int(res["nextPage"]) if res.get("nextPage") else None
    return out


def commons(params):
    q = urllib.parse.urlencode({"format": "json", "formatversion": "2", **params})
    req = urllib.request.Request(f"{COMMONS_API}?{q}", headers={"User-Agent": USER_AGENT})
    time.sleep(COMMONS_PAUSE_S)
    return json.loads(http(req))


def category_pageids(cat):
    ids, cont = [], {}
    while True:
        d = commons({"action": "query", "list": "categorymembers", "cmtitle": f"Category:{cat}",
                     "cmtype": "file", "cmlimit": "500", "cmprop": "ids", **cont})
        ids += [m["pageid"] for m in d["query"]["categorymembers"]]
        if "continue" not in d:
            return ids
        cont = {"cmcontinue": d["continue"]["cmcontinue"]}


def plain(s, limit):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= limit else s[: limit - 3].rstrip() + "..."


def image_infos(pageids):
    d = commons({"action": "query", "pageids": "|".join(map(str, pageids)), "prop": "imageinfo",
                 "iiprop": "url|size|mime|extmetadata", "iiurlwidth": str(THUMB_WIDTH)})
    for p in d["query"]["pages"]:
        ii = (p.get("imageinfo") or [None])[0]
        if not ii:
            continue
        m = ii.get("extmetadata", {})
        title = plain(m.get("ObjectName", {}).get("value") or p["title"].removeprefix("File:").rsplit(".", 1)[0], 90)
        yield {
            "pageid": p["pageid"], "width": ii["width"], "height": ii["height"], "mime": ii["mime"],
            "thumb": ii.get("thumburl") or ii["url"],
            "license": plain(m.get("LicenseShortName", {}).get("value"), 40),
            "credit": f"{title} - {plain(m.get('Artist', {}).get('value'), 50) or 'Unknown'}, "
                      f"{plain(m.get('LicenseShortName', {}).get('value'), 40)}, Wikimedia Commons",
        }


def keep(info):
    aspect = info["width"] / max(info["height"], 1)
    return (info["mime"] == "image/jpeg" and info["width"] >= MIN_WIDTH
            and MIN_ASPECT <= aspect <= MAX_ASPECT and LICENSE_OK.match(info["license"] or ""))


def main():
    if not API_KEY:
        sys.exit("IMMICH_KEY_WALL is not set")
    log(f"scenery: DRY_RUN={DRY_RUN} album={ALBUM_NAME!r} add={ADD_PER_RUN} cap={ALBUM_CAP}")

    album_id = find_or_create_album()
    existing = album_assets(album_id) if album_id else []
    have = {int(m.group(1)) for a in existing if (m := FILENAME_RE.match(a.get("originalFileName", "")))}
    log(f"album has {len(existing)} assets ({len(have)} from Commons)")

    pool = set()
    for cat in CATEGORIES:
        ids = category_pageids(cat)
        log(f"  {cat}: {len(ids)} files")
        pool.update(ids)
    candidates = list(pool - have)
    random.shuffle(candidates)
    log(f"candidate pool {len(pool)}, {len(candidates)} not yet in the album")

    added, rejected = 0, 0
    for i in range(0, len(candidates), 50):
        if added >= ADD_PER_RUN:
            break
        for info in image_infos(candidates[i:i + 50]):
            if added >= ADD_PER_RUN:
                break
            if not keep(info):
                rejected += 1
                continue
            log(f"  + {info['credit']}")
            if not DRY_RUN:
                blob = http(urllib.request.Request(info["thumb"], headers={"User-Agent": USER_AGENT}))
                res = immich_upload(f"wmc-{info['pageid']}.jpg", blob)
                immich("PUT", f"/assets/{res['id']}", {"description": info["credit"]})
                immich("PUT", f"/albums/{album_id}/assets", {"ids": [res["id"]]})
            added += 1
    log(f"added {added}, rejected {rejected} (size, aspect, mime or licence)")

    over = len(existing) + added - ALBUM_CAP
    if over > 0:
        stale = [a["id"] for a in existing[:over]]
        log(f"pruning {len(stale)} oldest assets past cap {ALBUM_CAP}")
        if not DRY_RUN:
            immich("DELETE", "/assets", {"ids": stale, "force": True})
    log("done")


if __name__ == "__main__":
    main()
