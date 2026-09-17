# playground-server

Dedicated server for `unity-core-playground`, so the ParrelSync clones and anything else on the LAN can play against a server nobody has to host.

It is off by default. Scale it up when you want to play, down when you are done:

```bash
kubectl -n playground-server scale deploy/playground-server --replicas=1
kubectl -n playground-server scale deploy/playground-server --replicas=0
```

The playground repo wraps both in `scripts/server/ctl.sh up|down|status|logs`.

| | |
|---|---|
| Node | `hp-victus` (192.168.1.243), the idle x86 machine |
| Address for clients | `192.168.1.243:7770` (UDP, FishNet Tugboat) |
| Image | `git.kblab.me/kblack0610/unity-playground-server`, built from a tag by `scripts/build.sh <tag> server` and pushed by `scripts/server/image.sh` |

A clone joins it by putting `client@192.168.1.243:7770` in its `.parrelsyncarg`.

## Why it looks like this

- **No `replicas` field.** Flux uses server-side apply, so a field that is not in git is not owned by Flux and a manual `kubectl scale` sticks. Adding `replicas: 0` here would fight every scale-up.
- **`hostNetwork` and a pinned node.** A Unity Linux server build is x86_64 only, and the cluster is mostly Raspberry Pis, so it has to land on `hp-victus` or `asus-laptop`. Binding the node's own LAN address also avoids servicelb's per-node UDP proxy pods. To move it, change the `kubernetes.io/hostname` selector.
- **A pinned image tag.** The playground builds the server from a git tag, so the version that is running is written down here and a scale-up cannot quietly pick up a different build. Bump this line to deploy a new one.
- **No probes.** The server speaks UDP only, and a dead process is restarted by the kubelet anyway.
