# notes-sync-bridge

Tiny Python HTTP bridge that turns a Forgejo push webhook into a fan-out to
MQTT (for desktops) + ntfy (for the phone). Powers the few-seconds pull leg
of the `~/.notes` multi-device sync.

```
Forgejo /kblack0610/.notes push event
        ↓ POST /hook  (HMAC-SHA256 verified against FORGEJO_HMAC_SECRET)
notes-sync-bridge:8080
        ├──→ mosquitto.mosquitto.svc.cluster.local:1883  topic notes/sync/needed
        └──→ ntfy.ntfy.svc.cluster.local/notes-sync       (HTTP POST)
```

## Why Python + apk-add at startup

The whole bridge is ~120 lines of stdlib Python. The only non-stdlib piece
is `mosquitto_pub`. Rather than build a custom image, we use
`python:3.12-alpine` and `apk add --no-cache mosquitto-clients` at container
startup (~2s on cold start). Trade-off accepted: the bridge isn't on a hot
path, and avoiding a custom image keeps the supply chain simple.

If startup latency ever matters, bake the image:

```
FROM python:3.12-alpine
RUN apk add --no-cache mosquitto-clients
COPY bridge.py /app/bridge.py
CMD ["python", "/app/bridge.py"]
```

…push to `git.kblab.me/kblack0610/notes-sync-bridge:latest` and update
`deployment.yaml`.

## Secrets

`secret.yaml` (SOPS-encrypted) holds `FORGEJO_HMAC_SECRET` — a 64-char hex
string shared with the Forgejo webhook config. Rotate by:

```bash
NEW=$(head -c 32 /dev/urandom | xxd -p -c 64)
cd ~/dev/home/home-config
sops apps/notes-sync-bridge/secret.yaml   # edit, replace the value with $NEW
# then update the Forgejo webhook to match
curl -u "kblack0610:$FORGEJO_API_TOKEN" -X PATCH \
  -H 'Content-Type: application/json' \
  -d "{\"config\":{\"secret\":\"$NEW\"}}" \
  https://git.kblab.me/api/v1/repos/kblack0610/.notes/hooks/<id>
```

## Verification

```bash
# Health
kubectl --context home-k3s -n notes-sync-bridge get pods
curl -s https://notes-sync-bridge.kblab.me/health

# Bad HMAC -> 401
curl -s -X POST -H 'X-Forgejo-Signature: deadbeef' \
  -d '{"ref":"refs/heads/master"}' https://notes-sync-bridge.kblab.me/hook

# Good HMAC (from inside cluster, where SOPS-decrypted secret is mounted)
kubectl --context home-k3s -n notes-sync-bridge exec deploy/notes-sync-bridge -- /bin/sh -c '
  payload=$(printf "{\"ref\":\"refs/heads/master\"}")
  sig=$(printf "%s" "$payload" | openssl dgst -sha256 -hmac "$FORGEJO_HMAC_SECRET" -hex | awk "{print \$2}")
  wget -O- --header "X-Forgejo-Signature: $sig" --post-data "$payload" http://localhost:8080/hook
'
```
