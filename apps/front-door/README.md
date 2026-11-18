# Front door

`https://kblab.me` is one static page: every site and machine we run, grouped by category, with a link to each. It is built on web-kit, the shell every kblab site shares (dotfiles `.local/src/web-kit`: tokens, fonts, `<kb-shell>`), and adds only a few lines of CSS for the list. Live up/down is not on the page; its Status link goes to status.kblab.me.

## How the list is made

`scripts/gen-ha-launcher.py` walks every Flux-deployed ingress (`apps/*/ingress*.yaml`) and the bare-metal hosts in `ansible/inventory.yml`, and writes `index.html` here, together with HA's site status sensors and the blackbox probe list, so all three name the same hosts. Nobody edits `index.html` by hand.

- Groups: `NAMESPACE_GROUP` at the top of the script. Every namespace with a private host must be in it; an unmapped one stops the script with the namespace named, so a new app is never left uncategorized. Public domains form the Public group; the inventory hosts form Bare metal.
- Opt a host out with `homepage.kblab.me/launcher: "false"` on its ingress.
- Run it after adding, renaming or removing an ingress or inventory host, then commit the outputs. A second run prints `No changes.`

```bash
./scripts/gen-ha-launcher.py
```

## The kit pin

`kit.lock` pins web-kit: line 1 is a dotfiles commit, the rest is `sha256sum` output for each kit file at that commit. The pod's `fetch-kit` init container (`fetch-kit.sh`) downloads those files from GitHub and checks every one; a mismatch fails the init container, so the pod never serves an unpinned kit. Trader pins the same kit the same way (`web/kit.lock` in its repo).

To take a newer kit, set the commit and the hashes from that commit:

```bash
c=$(git -C ~/.dotfiles rev-parse origin/main)
{ echo "commit $c"; for f in tokens.css shell.css kb-shell.js inter.woff2 jetbrains-mono.woff2 OFL-inter.txt OFL-jetbrains-mono.txt; do
  echo "$(git -C ~/.dotfiles show "$c:.local/src/web-kit/$f" | sha256sum | cut -d' ' -f1)  $f"; done; } > apps/front-door/kit.lock
```

## Access

The host is reachable off the LAN through the wildcard Cloudflare tunnel, as the Home Assistant redirect it replaced was. crowdsec sits in front. The page lists names and links only, and every site behind it keeps its own sign-in.

## Verify

```bash
kubectl --context home-k3s -n front-door get pods,ingress
curl -s -o /dev/null -w '%{http_code}\n' https://kblab.me/            # 200
curl -s -o /dev/null -w '%{http_code}\n' https://kblab.me/kit/tokens.css   # 200
```

| Symptom | Cause and fix |
|---|---|
| Pod stuck in `Init:Error` | `kubectl -n front-door logs deploy/front-door -c fetch-kit`. `FAILED` on a file means `kit.lock` does not match its commit: regenerate it with the snippet above. A wget error means GitHub was unreachable; the pod retries. |
| A new app is missing | The generator was not rerun after its ingress landed, or its ingress opts out. |
