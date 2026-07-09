#!/usr/bin/env python3
"""Immich wall-frame curation job.

Builds a curated "best of" album per account so the wall tablets (immich-kiosk)
show great family photos instead of the whole library (screenshots, memes,
documents, receipts, blur). No LLM: it leans on signals Immich already has -
People/* tags (from the Google-Takeout face groups), favorites/ratings, album
membership, GPS, mimetype - plus Immich's own CLIP smart-search as a soft
negative for screenshot/meme/document-shaped assets.

Pure stdlib (urllib + json) so it runs in a stock python:3.12-slim container with
no pip install, matching the apps/spotify-concerts convention.

Design notes (calibrated against the live 2.6.3 API on 2026-07-08):
  - Google-Takeout imports carry NO camera EXIF (make/model) and NO facial-
    recognition people, so those are NOT used as signals. ~36% of the sampled
    library does carry a People/<name> tag - that is the primary keeper signal.
  - Smart search returns results ranked but exposes NO similarity score, and it
    ranks real HEIC photos highly for "screenshot". So CLIP is a SOFT negative
    (top-N per junk query), never a hard exclude - the score math lets a
    People-tagged photo outweigh a spurious junk match.
  - Everything is a tunable env var; run once with DRY_RUN=true and read the
    logged distribution before letting it write.

Idempotent: re-running syncs the album to the computed set (adds new winners,
removes assets that no longer qualify).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------- #
# Config (all env-tunable; no code edit needed to retune from the manifest).
# ---------------------------------------------------------------------------- #
IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich-server").rstrip("/")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() not in ("false", "0", "no")

KEEPER_TAG_PREFIX = os.environ.get("KEEPER_TAG_PREFIX", "People/")
KEEP_FLOOR = float(os.environ.get("KEEP_FLOOR", "4"))     # min score to include
TARGET_MAX = int(os.environ.get("TARGET_MAX", "8000"))    # cap album size
MIN_ALBUM = int(os.environ.get("MIN_ALBUM", "300"))       # fallback floor if sparse
MIN_LONG_EDGE = int(os.environ.get("MIN_LONG_EDGE", "900"))
MAX_ASPECT = float(os.environ.get("MAX_ASPECT", "2.4"))
JUNK_TOPN = int(os.environ.get("JUNK_TOPN", "1500"))      # per junk query
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "1000"))
ADD_CHUNK = 500

JUNK_QUERIES = [q.strip() for q in os.environ.get(
    "JUNK_QUERIES",
    "a screenshot of a phone screen|a document or receipt|a meme with text|"
    "a screenshot of an app|a scanned page of text",
).split("|") if q.strip()]

# Filename patterns that are reliably junk regardless of everything else.
SCREENSHOT_RE = re.compile(r"screen\s?shot|screencapture|scrnli|whitagram", re.I)

# Score weights.
W_PEOPLE_TAG = 5.0
W_FACE_PERSON = 5.0
W_FAVORITE = 4.0
W_ALBUM = 2.0
W_GPS = 1.0
W_MIME = {"image/heic": 1.0, "image/heif": 1.0, "image/jpeg": 0.5, "image/png": -1.0}
W_CLIP_JUNK = -3.0


# ---------------------------------------------------------------------------- #
# Minimal Immich REST client.
# ---------------------------------------------------------------------------- #
class Immich:
    def __init__(self, api_key):
        self.key = api_key

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"x-api-key": self.key, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            IMMICH_URL + path, data=data, headers=headers, method=method
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
            except urllib.error.URLError:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise

    def whoami(self):
        return self._req("GET", "/api/users/me")

    def tags(self):
        return self._req("GET", "/api/tags") or []

    def albums(self):
        return self._req("GET", "/api/albums") or []

    def album(self, album_id):
        return self._req("GET", f"/api/albums/{album_id}")

    def create_album(self, name):
        return self._req("POST", "/api/albums", {"albumName": name, "assetIds": []})

    def add_assets(self, album_id, ids):
        return self._req("PUT", f"/api/albums/{album_id}/assets", {"ids": ids})

    def remove_assets(self, album_id, ids):
        return self._req("DELETE", f"/api/albums/{album_id}/assets", {"ids": ids})

    def _search_all(self, path, base_body):
        """Page a search endpoint to exhaustion, yielding asset dicts."""
        page = 1
        while page:
            body = dict(base_body, page=page)
            res = self._req("POST", path, body)
            bucket = res.get("assets", {})
            for a in bucket.get("items", []):
                yield a
            nxt = bucket.get("nextPage")
            page = int(nxt) if nxt else None

    def metadata_all(self, extra=None):
        body = {"type": "IMAGE", "size": PAGE_SIZE, "withExif": True}
        if extra:
            body.update(extra)
        return self._search_all("/api/search/metadata", body)

    def smart_topn(self, query, n):
        """Top-n ranked asset ids for a smart-search query (no score exposed)."""
        ids = set()
        for a in self._search_all("/api/search/smart", {"query": query, "type": "IMAGE", "size": PAGE_SIZE}):
            ids.add(a["id"])
            if len(ids) >= n:
                break
        return ids


# ---------------------------------------------------------------------------- #
# Curation.
# ---------------------------------------------------------------------------- #
def is_hard_junk(a):
    """Reliable, mimetype-agnostic junk: bad filename, tiny, or extreme aspect."""
    fn = a.get("originalFileName") or ""
    if SCREENSHOT_RE.search(fn):
        return "filename"
    ex = a.get("exifInfo") or {}
    w = ex.get("exifImageWidth") or a.get("width") or 0
    h = ex.get("exifImageHeight") or a.get("height") or 0
    if w and h:
        if max(w, h) < MIN_LONG_EDGE:
            return "tiny"
        if max(w, h) / max(1, min(w, h)) > MAX_ASPECT:
            return "aspect"
    return None


def score(a, people_ids, album_ids, clip_junk_ids):
    ex = a.get("exifInfo") or {}
    s = 0.0
    if a["id"] in people_ids:
        s += W_PEOPLE_TAG
    if a.get("people"):                      # native facial recognition, when ready
        s += W_FACE_PERSON
    if a.get("isFavorite"):
        s += W_FAVORITE
    rating = ex.get("rating") or 0
    if rating > 0:
        s += float(rating)
    if a["id"] in album_ids:
        s += W_ALBUM
    if ex.get("latitude"):
        s += W_GPS
    s += W_MIME.get(a.get("originalMimeType"), 0.0)
    if a["id"] in clip_junk_ids:
        s += W_CLIP_JUNK
    return s


def curate_account(label, api_key, album_name):
    log = lambda m: print(f"[{label}] {m}", flush=True)
    im = Immich(api_key)
    me = im.whoami()
    log(f"account: {me.get('email')} (id {me.get('id','?')[:8]})")

    # 1. Keeper-tag id set (People/<name> tags -> union of tagged asset ids).
    keeper_tags = [t for t in im.tags() if t["value"].startswith(KEEPER_TAG_PREFIX)]
    people_ids = set()
    for t in keeper_tags:
        for a in im.metadata_all({"tagIds": [t["id"]]}):
            people_ids.add(a["id"])
    log(f"keeper-tag '{KEEPER_TAG_PREFIX}*': {len(keeper_tags)} tags -> {len(people_ids)} tagged assets")

    # 2. Existing real-album membership (exclude our own target album).
    album_ids = set()
    target_album = None
    for alb in im.albums():
        if alb["albumName"] == album_name:
            target_album = alb
            continue
        full = im.album(alb["id"])
        for a in (full.get("assets") or []):
            album_ids.add(a["id"])
    log(f"real-album members (excl. target): {len(album_ids)}")

    # 3. CLIP soft-negative set (top-N per junk query, union).
    clip_junk_ids = set()
    for q in JUNK_QUERIES:
        hits = im.smart_topn(q, JUNK_TOPN)
        clip_junk_ids |= hits
    log(f"CLIP junk-ranked (soft -3): {len(clip_junk_ids)} assets over {len(JUNK_QUERIES)} queries")

    # 4. Score every live IMAGE asset.
    kept, hard, stats = [], 0, {"png": 0, "heic": 0, "jpeg": 0, "other": 0}
    total = 0
    for a in im.metadata_all():
        if a.get("isArchived") or a.get("isTrashed") or a.get("isOffline"):
            continue
        if (a.get("visibility") or "timeline") != "timeline":
            continue
        total += 1
        m = (a.get("originalMimeType") or "").split("/")[-1]
        stats[m if m in stats else "other"] += 1
        if is_hard_junk(a):
            hard += 1
            continue
        s = score(a, people_ids, album_ids, clip_junk_ids)
        if s >= KEEP_FLOOR:
            kept.append((s, a["id"]))

    # 5. Fallback if the qualifying set is too thin (e.g. an account with no
    #    People tags / favorites yet) -> take the best-scored real photos so the
    #    frame is never empty.
    used_fallback = False
    if len(kept) < MIN_ALBUM:
        used_fallback = True
        pool = []
        for a in im.metadata_all():
            if a.get("isArchived") or a.get("isTrashed") or a.get("isOffline"):
                continue
            if is_hard_junk(a):
                continue
            pool.append((score(a, people_ids, album_ids, clip_junk_ids), a["id"]))
        kept = sorted(pool, reverse=True)[:max(MIN_ALBUM, min(len(pool), TARGET_MAX))]

    kept.sort(reverse=True)
    winners = [aid for _, aid in kept[:TARGET_MAX]]
    log(f"scanned {total} live images | hard-junk {hard} | mimetypes {stats}")
    log(f"qualified score>={KEEP_FLOOR}: {len(kept)}"
        + (" (FALLBACK to best real photos)" if used_fallback else "")
        + f" | album cap {TARGET_MAX} -> {len(winners)} winners")

    # 6. Sync the album idempotently.
    if target_album is None:
        if DRY_RUN:
            log(f"DRY_RUN: would CREATE album '{album_name}' with {len(winners)} assets")
            return
        target_album = im.create_album(album_name)
        current = set()
        log(f"created album '{album_name}' (id {target_album['id'][:8]})")
    else:
        current = {a["id"] for a in (im.album(target_album["id"]).get("assets") or [])}

    want = set(winners)
    to_add = [i for i in winners if i not in current]
    to_remove = [i for i in current if i not in want]
    log(f"album now {len(current)} | +{len(to_add)} -{len(to_remove)} -> {len(want)}")

    if DRY_RUN:
        log("DRY_RUN: no writes. Sample winners: "
            + ", ".join(w[:8] for w in winners[:5]))
        return

    for i in range(0, len(to_add), ADD_CHUNK):
        im.add_assets(target_album["id"], to_add[i:i + ADD_CHUNK])
    for i in range(0, len(to_remove), ADD_CHUNK):
        im.remove_assets(target_album["id"], to_remove[i:i + ADD_CHUNK])
    log("album sync complete")


def main():
    # Accounts are defined by paired env vars: IMMICH_KEY_<LABEL> + ALBUM_NAME_<LABEL>.
    accounts = []
    for var, key in os.environ.items():
        if var.startswith("IMMICH_KEY_") and key.strip():
            label = var[len("IMMICH_KEY_"):]
            album = os.environ.get(f"ALBUM_NAME_{label}", f"Wall Best ({label.title()})")
            accounts.append((label, key.strip(), album))
    if not accounts:
        print("no accounts configured (set IMMICH_KEY_<LABEL>); nothing to do", flush=True)
        return
    print(f"immich-curate | url={IMMICH_URL} | DRY_RUN={DRY_RUN} | "
          f"{len(accounts)} account(s): {[a[0] for a in accounts]}", flush=True)
    failures = 0
    for label, key, album in sorted(accounts):
        try:
            curate_account(label, key, album)
        except Exception as e:                # keep going; one bad key != total fail
            failures += 1
            print(f"[{label}] ERROR: {e}", flush=True)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
