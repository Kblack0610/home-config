#!/usr/bin/env python3
"""One-time Spotify OAuth → refresh token for spotify-concerts.

Run this ONCE on your workstation to mint a long-lived refresh token that the
service uses to read your top/followed artists. Pure stdlib.

Prereqs (see README):
  1. Create a Spotify app at https://developer.spotify.com/dashboard
  2. Add redirect URI EXACTLY:  http://127.0.0.1:8080/callback
  3. Copy the Client ID + Client secret.

Usage:
  SPOTIFY_CLIENT_ID=xxxx SPOTIFY_CLIENT_SECRET=yyyy python3 bootstrap-oauth.py

It prints your refresh token — hand it to Claude (or drop it in the secret).
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
SCOPES = "user-top-read user-follow-read"

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
            self.wfile.write(b"Authorized. You can close this tab and return to the terminal.")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *a):
        return


print("Opening your browser to authorize Spotify...")
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
req = urllib.request.Request(
    "https://accounts.spotify.com/api/token", data=body,
    headers={"Authorization": f"Basic {basic}",
             "Content-Type": "application/x-www-form-urlencoded"},
)
tok = json.load(urllib.request.urlopen(req))
print("\n" + "=" * 60)
print("YOUR SPOTIFY REFRESH TOKEN (paste to Claude / into the secret):\n")
print(tok["refresh_token"])
print("=" * 60)
