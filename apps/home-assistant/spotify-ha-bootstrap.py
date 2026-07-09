#!/usr/bin/env python3
"""One-time Spotify OAuth → refresh token for the OFFICIAL HA `spotify` integration.

Run ONCE on your workstation. It uses the EXACT scope set Home Assistant's
spotify integration requests, so the resulting refresh token drives the HA media
module — AND (since HA's scopes are a superset) the spotify-concerts bridge too.
Pure stdlib. Requires a Spotify **Premium** developer app.

Prereqs:
  1. Create a Spotify app at https://developer.spotify.com/dashboard (Premium).
  2. Add redirect URIs (both):
       http://127.0.0.1:8080/callback              (this script)
       https://my.home-assistant.io/redirect/oauth (the HA integration itself)
  3. Copy Client ID + Client secret.

Usage:
  SPOTIFY_CLIENT_ID=xxxx SPOTIFY_CLIENT_SECRET=yyyy \
    python3 apps/home-assistant/spotify-ha-bootstrap.py

It prints the refresh token + your Spotify user id + display name — the values
the seed-spotify init container needs. Hand them to Claude via rbw.
"""
import base64
import json
import os
import secrets
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

CID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
CSEC = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
REDIRECT = "http://127.0.0.1:8080/callback"
# EXACT list from homeassistant/components/spotify/const.py SPOTIFY_SCOPES,
# comma-joined exactly as HA sends it to Spotify's /authorize.
SCOPES = ",".join([
    "user-modify-playback-state", "user-read-playback-state", "user-read-private",
    "playlist-read-private", "playlist-read-collaborative", "user-library-read",
    "user-top-read", "user-read-playback-position", "user-read-recently-played",
    "user-follow-read",
])

if not (CID and CSEC):
    raise SystemExit("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars first.")

state = secrets.token_urlsafe(8)
auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
    "client_id": CID, "response_type": "code", "redirect_uri": REDIRECT,
    "scope": SCOPES, "state": state,
})
got = {}


class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.parse_qs(u.query)
        if u.path == "/callback" and p.get("state", [""])[0] == state and "code" in p:
            got["code"] = p["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorized. Close this tab and return to the terminal.")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *a):
        return


print("Opening your browser to authorize Spotify (Premium)...")
print("If it doesn't open, paste this URL:\n" + auth_url + "\n")
try:
    webbrowser.open(auth_url)
except Exception:  # noqa: BLE001
    pass

srv = HTTPServer(("127.0.0.1", 8080), H)
while "code" not in got:
    srv.handle_request()

basic = base64.b64encode(f"{CID}:{CSEC}".encode()).decode()
body = urllib.parse.urlencode({
    "grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
}).encode()
tok = json.load(urllib.request.urlopen(urllib.request.Request(
    "https://accounts.spotify.com/api/token", data=body,
    headers={"Authorization": f"Basic {basic}",
             "Content-Type": "application/x-www-form-urlencoded"})))

# Fetch the Spotify profile → user id + display name (HA uses these).
me = json.load(urllib.request.urlopen(urllib.request.Request(
    "https://api.spotify.com/v1/me",
    headers={"Authorization": f"Bearer {tok['access_token']}"})))

print("\n" + "=" * 66)
print("Give these to Claude (via rbw item 'spotify-ha creds'):\n")
print(f"spotify-client-id:      {CID}")
print(f"spotify-client-secret:  {CSEC}")
print(f"spotify-refresh-token:  {tok['refresh_token']}")
print(f"spotify-user-id:        {me.get('id')}")
print(f"spotify-name:           {me.get('display_name')}")
print("=" * 66)
