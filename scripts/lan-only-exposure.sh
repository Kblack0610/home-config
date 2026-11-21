#!/usr/bin/env bash
# For every Ingress that carries the monitoring-local-network-only middleware, show
# whether the host answers through the Cloudflare tunnel (public) and on the LAN.
#
# The middleware cannot keep a host off the internet: Traefik sees every connection
# from a pod IP (the cloudflared pods for the tunnel, the svclb pod for the LAN), so
# no sourceRange tells them apart. A host is off the tunnel only when it has no
# public DNS record (external-dns.alpha.kubernetes.io/controller: none, and the old
# CNAME deleted); Cloudflare answers a Host header without a record with 530/1016.
#
# Usage: scripts/lan-only-exposure.sh   (needs kubectl on home-k3s, dig, curl, jq)
# Exit 1 when any host answers through the tunnel.
set -euo pipefail

PUBLIC_RESOLVER=1.1.1.1
exposed=0

hosts=$(kubectl --context home-k3s get ingress -A -o json | jq -r '
  .items[]
  | select((.metadata.annotations["traefik.ingress.kubernetes.io/router.middlewares"] // "") | test("local-network-only"))
  | .spec.rules[].host' | sort -u)

for host in $hosts; do
  cf_ip=$(dig +short "$host" @"$PUBLIC_RESOLVER" | grep -E '^[0-9.]+$' | tail -1 || true)
  tunnel=no-dns
  if [[ -n "$cf_ip" ]]; then
    tunnel=$(curl -s -o /dev/null -m 10 -w '%{http_code}' --resolve "$host:443:$cf_ip" "https://$host/" || true)
  fi
  lan=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "https://$host/" || true)
  verdict=off-tunnel
  if [[ "$tunnel" != no-dns ]]; then verdict=EXPOSED; exposed=1; fi
  printf '%-30s tunnel=%-7s lan=%-4s %s\n' "$host" "$tunnel" "$lan" "$verdict"
done
exit "$exposed"
