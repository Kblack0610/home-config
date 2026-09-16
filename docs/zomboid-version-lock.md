# Zomboid: the client/server version lock

Project Zomboid has no version negotiation between client and server. Steam auto-updates the client; `apps/zomboid/deployment.yaml` pins the server to a build-pinned image tag. When Steam pushes a client build and the pin is not moved to match, every join breaks.

## The symptom

The player sits on **"Joining game"** for about three minutes, then the client gives up. There is no error on either side. The server does not log a failure, does not log a kick, and does not log a timeout. `kubectl logs` looks completely healthy, the pod stays `Running`, and RCON answers normally, so every top-level health check passes while nobody can play.

This is the trap: a mismatched server is indistinguishable from a healthy one at every layer except the join sequence itself.

## The diagnostic

PZ's own logs in the PVC say far more than the console. The join sequence lives in `Logs/*_connections.txt`:

```bash
kubectl exec -n zomboid deploy/zomboid -c zomboid -- \
  tail -20 /home/steam/Zomboid/Logs/*_connections.txt
```

A **healthy** join runs all five steps:

```
receive-packet  login
receive-packet  client-connect
send-packet     connection-details
receive-packet  player-connect      <- client answers
event           fully-connected     <- player is in
```

A **version-mismatched** join stops dead after step three:

```
receive-packet  login
receive-packet  client-connect
send-packet     connection-details
(nothing, then disconnection-notification ~3 min later)
```

The server has done its whole job. It authenticated the user (`user.txt` shows `"<name>" allowed to join`) and sent connection-details. The client received a payload it could not parse and never replied. `grep -c 'fully connected' Logs/*_user.txt` returning `0` for the life of a pod means no one has successfully joined since it started.

## Confirming it

Compare the two Steam manifests. Note that the game (appid 108600) and the dedicated server (appid 380870) are **separate Steam apps with independent buildid sequences**, so their `buildid` values are never equal and comparing them for equality is meaningless. Compare `LastUpdated` instead: a healthy pair was published in the same upstream release wave, normally within a couple of hours.

Client (on the player's machine, appid 108600):

```bash
grep -E '"(buildid|LastUpdated)"' \
  ~/.local/share/Steam/steamapps/appmanifest_108600.acf
```

Server (inside the pod, appid 380870):

```bash
kubectl exec -n zomboid deploy/zomboid -c zomboid -- \
  grep -E '"(buildid|LastUpdated)"' \
  /home/steam/pz-dedicated/steamapps/appmanifest_380870.acf
```

Convert `LastUpdated` with `date -u -d @<epoch>`. If the client's timestamp is days newer than the server's, that is the fault. For reference, the 2026-09-16 outage read client 2026-08-26 11:45Z against server 2026-08-06 - a 20-day gap; after the fix the pair read 11:45Z and 12:45Z on the same day, which is what correct looks like.

Do this **before** investigating DNS, port-forwards, NAT reflection or hostPort - the network is provably fine the moment `client-connect` reaches the server at all.

## The fix

Find the tag whose publish date matches the client's `LastUpdated`:

```bash
curl -s "https://hub.docker.com/v2/repositories/danixu86/project-zomboid-dedicated-server/tags?page_size=25&ordering=last_updated" \
  | python3 -c "import sys,json; [print(f\"{t['name']:<28} {t['last_updated'][:19]}\") for t in json.load(sys.stdin)['results']]"
```

Upstream publishes a matching server image within a couple of hours of a client build. Bump the `image:` tag in `apps/zomboid/deployment.yaml`, commit, push to `forgejo`, and let Flux reconcile. Do not switch the pin to `latest-release`: an unpinned server would follow upstream independently of the clients and reintroduce the same mismatch in the other direction, and it would break the `deployment.yaml` guarantee that git names the exact running build.

## Why this is easy to misdiagnose

Nothing in git changes when this breaks. The manifests, the image tag, the ini and the network path are all byte-identical to the day the server last worked - the drift is entirely on the client side, outside the repo. A "what changed?" investigation that only looks at `git log` finds nothing and moves on to the network, which is the one layer already proven healthy.
