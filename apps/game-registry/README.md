# game-registry

The BNB game server registry (`bnb-game-servers/registry`, Go + SQLite): game servers register here for a join code and a place in the server list, and the playground posts match results and records to it for its leaderboards.

- URL: `https://game-registry.kblab.me` (LAN and tailnet only). In the cluster: `http://game-registry.game-registry.svc:8080`.
- Image: `git.kblab.me/kblack0610/bnb-game-servers/registry:<tag>`, published by that repo's Forgejo workflow on a `registry-v*` tag. Bump the tag in `deployment.yaml`.
- Tokens: `tokens-secret.yaml` (sops). `SERVER_TOKEN` is for dedicated servers, and its results move ratings; `playground-server` holds a copy in its own namespace (`apps/playground-server/registry-token-secret.yaml`), so rotate both together. `HOST_TOKEN` ships in every playground build (`Assets/Playground/Resources/Online/online.json`) and only registers servers and posts unverified results.
- Data: one SQLite file on the `game-registry-data` local-path volume.
- Routes and env: `registry/README.md` in bnb-game-servers.
