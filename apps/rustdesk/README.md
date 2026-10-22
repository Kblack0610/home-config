# RustDesk relay (hbbs + hbbr)

Self-hosted RustDesk ID and relay server, so a session between two machines on the LAN stays on the LAN instead of going out to RustDesk's public relays (`rs-ny.rustdesk.com`, `ovh-west1.rustdesk.com`). The main link it serves is the CachyOS desktop (client) driving the Kandji-managed work Mac (host). That Mac's MDM stealth firewall drops inbound connections, so a direct peer link usually fails and the session falls back to a relay; this makes that relay one hop away.

- **Endpoint:** `rustdesk.kblab.me` (AdGuard `*.kblab.me` rewrite to a node). `type: LoadBalancer`, so k3s servicelb binds the ports on every node and any node IP also works.
- **Ports:** TCP 21115 (NAT test), TCP+UDP 21116 (ID / rendezvous), TCP 21117 (relay). Web-client ports 21118/21119 are not exposed.
- **Key:** the server keypair is `secret.sops.yaml`, so it survives the pod moving. Clients need the public half:

  ```
  KpyKZeelfsY80iq0XqU6DoLe5kqcujNTpki3oSZCyi0=
  ```

- **Relay cap:** hbbr's defaults throttle each relayed session to 32 Mb/s, which is sized for a shared internet relay. `LIMIT_SPEED` / `SINGLE_BANDWIDTH` raise it to 512 Mb/s.

## Point a client at it

In the RustDesk GUI on each machine, Settings -> Network -> ID/Relay server:

- **ID server:** `rustdesk.kblab.me` (use a node IP such as `192.168.1.20` if the machine does not use AdGuard for DNS, which a VPN'd work laptop may not)
- **Relay server:** leave blank; the client then relays through the ID server host on 21117
- **Key:** the public key above

Do not hand-edit `RustDesk2.toml`; it also carries the client's password and key material. Both sides must use the same server, and each gets a new ID only if the old one was taken on this server (normally they keep their ID).

## Check it

```bash
kubectl --context home-k3s -n rustdesk logs deploy/rustdesk -c hbbs | grep -E 'Key:|Listening'
kubectl --context home-k3s -n rustdesk logs deploy/rustdesk -c hbbr -f   # relay traffic shows up here
nc -zv rustdesk.kblab.me 21116 && nc -zv rustdesk.kblab.me 21117
```

The `Key:` line must match the public key above. A client showing "Ready" in the bottom-left of the main window is registered.

## Rollback

Clear the ID server and key in each client's settings; it goes back to the public servers.
