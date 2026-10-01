#!/usr/bin/env bash
set -uo pipefail

# netcheck - "the internet is down": find the ONE broken box, without DNS.
#
# Walks the chain this house's name resolution depends on, using only IP
# addresses, so it still works when DNS is the thing that is broken:
#
#   this machine -> router dnsmasq (192.168.1.1) -> AdGuard on pi3 (192.168.1.193) -> internet
#
# and prints a single verdict naming what to fix. Every run is also saved to
# ~/.local/state/netcheck/ so there is a record of what the outage looked like.
#
# Every status page here is reached by a *.kblab.me name, so when DNS breaks
# none of them load; and power-cycling the router first erases its RAM-only log
# without fixing a dead pi3. See ansible/roles/hw-watchdog/README.md.
#
# Usage: netcheck            run the checks, print verdict + evidence
#        netcheck --quick    skip the SSH/Prometheus evidence section
#
# Every address is overridable, so a negative control can point a probe at a
# dead IP without breaking anything real:  NETCHECK_ADGUARD=192.168.1.254 netcheck

ROUTER="${NETCHECK_ROUTER:-192.168.1.1}"
ADGUARD="${NETCHECK_ADGUARD:-192.168.1.193}"
ADGUARD_SSH="${NETCHECK_ADGUARD_SSH:-kblack0610@${ADGUARD}}"
PUBLIC_DNS="${NETCHECK_PUBLIC_DNS:-1.1.1.1}"
REWRITE_NAME="${NETCHECK_REWRITE_NAME:-git.kblab.me}"
REWRITE_IP="${NETCHECK_REWRITE_IP:-192.168.1.124}"
PROM="${NETCHECK_PROM:-http://192.168.1.20:30090}"
NETCONSOLE_HOST="${NETCHECK_NETCONSOLE_HOST:-kblack0610@192.168.1.20}"
STATE_DIR="${NETCHECK_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/netcheck}"

# Probe timeout, seconds. Short on purpose: a dead hop should cost 2s, not 15.
T=2

# verdict: pure function of the probe results in R (1 = pass, 0 = fail).
# Kept free of I/O so tests/netcheck-verdict.sh can drive it with fake results.
# Order matters: each rule assumes every rule above it passed.
declare -A R

verdict() {
    if [[ "${R[link]}" != 1 ]]; then
        echo "NO LINK|This machine has no network link. Cable, switch port or NIC. Restarting the router will not help."
    elif [[ "${R[router_ping]}" != 1 ]]; then
        echo "ROUTER DOWN|Router ${ROUTER} is not answering ping. Power-cycle the router (that erases its RAM-only log, so this output is the record)."
    elif [[ "${R[pi_ping]}" != 1 ]]; then
        echo "ADGUARD PI DOWN|pi3 (${ADGUARD}) is not answering ping, and the whole house resolves through it. Power-cycle ONLY the Pi, leave the router alone. Its hardware watchdog should reboot a frozen Pi within ~15s, so if it stays down, suspect power (PSU/cable)."
    elif [[ "${R[internet_ping]}" != 1 && "${R[dns_public]}" != 1 ]]; then
        echo "INTERNET DOWN|LAN and DNS servers are up, but nothing past the router answers (${PUBLIC_DNS} unreachable). ISP/WAN outage - nothing in the house to restart. *.kblab.me names should still resolve."
    elif [[ "${R[dns_adguard]}" != 1 && "${R[pi_ssh]}" == 1 ]]; then
        echo "ADGUARD HUNG|pi3 is alive but AdGuard is not answering DNS. Restart just the container: ssh ${ADGUARD_SSH} 'docker restart adguard-home'"
    elif [[ "${R[dns_adguard]}" != 1 ]]; then
        echo "PI HALF-ALIVE|pi3 answers ping but neither SSH nor DNS - the kernel is up and userspace is wedged. Power-cycle ONLY the Pi."
    elif [[ "${R[dns_rewrite]}" != 1 ]]; then
        echo "REWRITES BROKEN|AdGuard resolves internet names but ${REWRITE_NAME} no longer points at ${REWRITE_IP}, so every *.kblab.me service looks down. Check the AdGuard rewrite rules."
    elif [[ "${R[dns_router]}" != 1 ]]; then
        echo "ROUTER DNS|AdGuard is fine but the router's dnsmasq is not forwarding. ssh root@${ROUTER} '/etc/init.d/dnsmasq restart'"
    elif [[ "${R[dns_local]}" != 1 ]]; then
        echo "LOCAL RESOLVER|The whole chain works; only this machine cannot resolve. Run: resolvectl status; sudo resolvectl flush-caches"
    else
        echo "HEALTHY|DNS chain healthy end to end."
    fi
}

# Allow tests to source the verdict without running probes.
[[ "${NETCHECK_SOURCE_ONLY:-0}" == 1 ]] && return 0

ping1()   { ping -c1 -W"$T" "$1" >/dev/null 2>&1; }
dig1()    { dig +short +time="$T" +tries=1 "$@" 2>/dev/null; }
resolves(){ [[ -n "$(dig1 "$1" "@$2" | grep -E '^[0-9a-f.:]+$' | head -1)" ]]; }
tcp_open(){ timeout "$T" bash -c "</dev/tcp/$1/$2" >/dev/null 2>&1; }

iface="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"

probe() {  # probe <key> <label> <command...>
    local key="$1" label="$2"; shift 2
    if "$@"; then R[$key]=1; printf '  \e[32mOK  \e[0m %s\n' "$label"
    else          R[$key]=0; printf '  \e[31mFAIL\e[0m %s\n' "$label"; fi
}

link_up() { [[ -n "$iface" && "$(cat "/sys/class/net/$iface/carrier" 2>/dev/null)" == 1 ]]; }
rewrite_ok() { [[ "$(dig1 "$REWRITE_NAME" "@$ADGUARD" | tail -1)" == "$REWRITE_IP" ]]; }
local_ok() { getent hosts google.com >/dev/null 2>&1; }

run() {
    echo "netcheck $(date '+%Y-%m-%d %H:%M:%S %Z') on $(hostname)"
    echo
    echo "Chain: this machine -> router ${ROUTER} -> AdGuard ${ADGUARD} -> internet"
    probe link          "link up on ${iface:-<no default route>}"          link_up
    probe router_ping   "router ${ROUTER} answers ping"                   ping1 "$ROUTER"
    probe pi_ping       "AdGuard pi3 ${ADGUARD} answers ping"             ping1 "$ADGUARD"
    probe pi_ssh        "pi3 SSH port open"                               tcp_open "$ADGUARD" 22
    probe internet_ping "internet ${PUBLIC_DNS} answers ping"             ping1 "$PUBLIC_DNS"
    probe dns_public    "public DNS ${PUBLIC_DNS} resolves google.com"     resolves google.com "$PUBLIC_DNS"
    probe dns_adguard   "AdGuard resolves google.com"                     resolves google.com "$ADGUARD"
    probe dns_rewrite   "AdGuard maps ${REWRITE_NAME} -> ${REWRITE_IP}"    rewrite_ok
    probe dns_router    "router dnsmasq resolves google.com"              resolves google.com "$ROUTER"
    probe dns_local     "this machine resolves google.com"                local_ok

    local v; v="$(verdict)"
    echo
    printf '\e[1mVERDICT: %s\e[0m\n  %s\n' "${v%%|*}" "${v#*|}"

    [[ "${1:-}" == --quick ]] && { [[ "${v%%|*}" == HEALTHY ]]; return; }

    echo
    echo "Evidence (best effort, all by IP):"
    if [[ "${R[pi_ssh]}" == 1 ]]; then
        echo "  pi3 uptime:      $(timeout 5 ssh -o BatchMode=yes -o ConnectTimeout=$T -o LogLevel=ERROR "$ADGUARD_SSH" 'uptime -p; vcgencmd get_throttled' 2>/dev/null | paste -sd' ' || echo unavailable)"
    fi
    echo "  router uptime:   $(timeout 5 ssh -o BatchMode=yes -o ConnectTimeout=$T -o LogLevel=ERROR "root@${ROUTER}" 'cut -d. -f1 /proc/uptime' 2>/dev/null | awk '{printf "%dm\n", $1/60}' || echo unavailable)"
    echo "  firing alerts:   $(curl -s -m 4 "${PROM}/api/v1/alerts" 2>/dev/null | jq -r '[.data.alerts[] | select(.state=="firing") | .labels.alertname] | unique | join(", ") | if .=="" then "none" else . end' 2>/dev/null || echo "Prometheus unreachable")"
    echo "  pi3 kernel log (netconsole on ${NETCONSOLE_HOST#*@}, last 5):"
    timeout 6 ssh -o BatchMode=yes -o ConnectTimeout=$T -o LogLevel=ERROR "$NETCONSOLE_HOST" \
        "journalctl -u netconsole-receiver --no-pager -o short-iso -n 5 2>/dev/null | grep -v '^-- '" 2>/dev/null \
        | sed 's/^/    /' || echo "    unavailable"
    echo
    echo "Graphs without DNS: Grafana http://192.168.1.20:30300  Alerts ${PROM}/alerts"
    [[ "${v%%|*}" == HEALTHY ]]
}

mkdir -p "$STATE_DIR"
log="$STATE_DIR/$(date +%Y-%m-%dT%H%M%S).log"
run "${1:-}" 2>&1 | tee >(sed 's/\x1b\[[0-9;]*m//g' > "$log")
rc=${PIPESTATUS[0]}
echo "(saved: $log)"
exit "$rc"
