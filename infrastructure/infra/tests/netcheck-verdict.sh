#!/usr/bin/env bash
# Drives netcheck's verdict() with synthetic probe results, one row per outage
# shape, so the decision order can be checked without breaking a real network.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NETCHECK_SOURCE_ONLY=1 source "$here/../netcheck.sh"

keys=(link router_ping pi_ping pi_ssh internet_ping dns_public dns_adguard dns_rewrite dns_router dns_local)
fail=0

# case <expected verdict> <space-separated keys that FAIL>
case_() {
    local want="$1"; shift
    local k; for k in "${keys[@]}"; do R[$k]=1; done
    for k in "$@"; do R[$k]=0; done
    local got; got="$(verdict)"; got="${got%%|*}"
    if [[ "$got" == "$want" ]]; then
        printf 'ok    %-16s <- fails: %s\n' "$want" "${*:-none}"
    else
        printf 'FAIL  want %-16s got %-16s <- fails: %s\n' "$want" "$got" "${*:-none}"
        fail=1
    fi
}

case_ HEALTHY
case_ "NO LINK"         link router_ping pi_ping pi_ssh internet_ping dns_public dns_adguard dns_rewrite dns_router dns_local
case_ "ROUTER DOWN"     router_ping dns_router dns_local
# 2026-09-30: pi3 frozen. Everything behind it fails, the router itself is fine.
case_ "ADGUARD PI DOWN" pi_ping pi_ssh dns_adguard dns_rewrite dns_router dns_local
case_ "INTERNET DOWN"   internet_ping dns_public dns_adguard dns_router dns_local
case_ "ADGUARD HUNG"    dns_adguard dns_rewrite dns_router dns_local
case_ "PI HALF-ALIVE"   pi_ssh dns_adguard dns_rewrite dns_router dns_local
case_ "REWRITES BROKEN" dns_rewrite
case_ "ROUTER DNS"      dns_router dns_local
case_ "LOCAL RESOLVER"  dns_local
# Internet down must not be mistaken for AdGuard: AdGuard cannot resolve
# google.com without its upstream, but the Pi itself answers fine.
case_ "INTERNET DOWN"   internet_ping dns_public dns_adguard

exit "$fail"
