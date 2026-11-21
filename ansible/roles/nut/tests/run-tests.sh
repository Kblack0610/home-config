#!/bin/bash
# Replay UPS status sequences through the real NUT daemons with this role's
# rendered primary config, and check when the primary shuts everything down
# (why: docs/nut-ups.md, the 2026-11-17 LB-on-line-power row). Cases:
#   control    the deleted `AT LOWBATT` rule put back: LB on line power DOES
#              shut down, so the harness can see that failure.
#   lb-on-line LB on line power (OL DISCHRG LB): no shutdown.
#   ob-lb      on battery, then low battery: FSD and SHUTDOWNCMD run.
#   settle     on battery past the settle timer: FSD.
#   blip       on battery, back on line inside the settle timer: no FSD.
# It replaces /sbin/shutdown and /etc/nut, so it runs only in a container:
# locally it starts debian:trixie in docker; CI runs it in a debian:trixie job
# container with NUT_REPLAY_IN_CONTAINER=1.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROLE="$(cd "$HERE/.." && pwd)"

if [ "${NUT_REPLAY_IN_CONTAINER:-}" != 1 ]; then
    REPO="$(cd "$ROLE/../../.." && pwd)"
    exec docker run --rm -e NUT_REPLAY_IN_CONTAINER=1 -v "$REPO:/src:ro" \
        debian:trixie bash /src/ansible/roles/nut/tests/run-tests.sh
fi
[ -f /.dockerenv ] || [ -n "${container:-}" ] || [ -n "${CI:-}" ] \
    || { echo "refusing: not in a container (this replaces /sbin/shutdown)" >&2; exit 2; }

if ! command -v upsmon >/dev/null || ! command -v busybox >/dev/null; then
    apt-get update -qq >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        nut-server nut-client python3-jinja2 busybox bsdutils procps >/dev/null
fi
NUTBIN=/lib/nut
[ -x "$NUTBIN/dummy-ups" ] || NUTBIN=/usr/lib/nut
echo "NUT $(upsd -V 2>&1 | head -1)"

SETTLE=10       # nut_onbatt_settle_seconds for the test, 300 in production
FINALDELAY=2    # nut_primary_finaldelay for the test, 60 in production
# NUT tells the test nothing but its log and ups.status, so the test samples
# them: one cadence, every wait bounded by its own timeout.
SAMPLE=0.5
# Absence of a shutdown can only be shown over a window: three of upsmon's
# 5 s polls (POLLFREQALERT) after the event it would have acted on.
HOLD=15

OUT=/tmp/nut-replay
LOG=$OUT/syslog
failures=0

# CI job containers on one runner pod share its network, so two replays at once
# both bind 127.0.0.1:3493 and one reads the other's UPS. Each run takes a free port.
port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for _ in $(seq 20); do PORT=$((20000 + RANDOM % 20000)); port_free "$PORT" && break; done
port_free "$PORT" || { echo "no free port for upsd" >&2; exit 2; }
UPS="cyberpower@localhost:$PORT"

render() {  # render <template> <dest>
    python3 - "$ROLE/templates/$1" "$2" "$SETTLE" "$FINALDELAY" "$PORT" <<'EOF'
import sys, jinja2
src, dest, settle, finaldelay, port = sys.argv[1:]
env = jinja2.Environment(keep_trailing_newline=True, undefined=jinja2.StrictUndefined)
text = env.from_string(open(src).read()).render(
    ansible_managed="rendered by tests/run-tests.sh", inventory_hostname="replay",
    ansible_host="127.0.0.1", ansible_os_family="Debian", nut_role="server",
    nut_ups_name="cyberpower", nut_upsmon_user="upsmon",
    nut_upsmon_password="replay-test-password", nut_listen_address="127.0.0.1",
    nut_listen_port=int(port), nut_onbatt_settle_seconds=int(settle),
    nut_primary_finaldelay=int(finaldelay), nut_ntfy_url="http://127.0.0.1:9/none",
    nut_confdir="/etc/nut")
open(dest, "w").write(text)
EOF
}

stop_daemons() {
    pkill -x upsmon; pkill -x upsd; pkill -x dummy-ups; pkill -x upssched
    for _ in $(seq 40); do
        pgrep -x 'upsmon|upsd|dummy-ups|upssched' >/dev/null || return 0
        sleep "$SAMPLE"
    done
    pkill -9 -x 'upsmon|upsd|dummy-ups|upssched'
}

setup() {  # setup <case> <seq text> [extra upssched.conf line]
    stop_daemons
    pkill -f 'busybox syslogd'
    rm -rf "$OUT" /etc/nut/* /run/nut /var/lib/nut-outage /etc/killpower
    mkdir -p "$OUT" /var/lib/nut-outage
    install -d -o nut -g nut -m 0770 /run/nut
    busybox syslogd -O "$LOG"
    for t in upsd.conf upsd.users nut.conf upssched.conf notifycmd.sh upssched-cmd \
             cluster-graceful-shutdown.sh primary-shutdown.sh; do
        render "$t.j2" "/etc/nut/$t"
    done
    render upsmon.server.conf.j2 /etc/nut/upsmon.conf
    grep -q "^MONITOR $UPS " /etc/nut/upsmon.conf || return 1
    [ -n "${3:-}" ] && echo "$3" >> /etc/nut/upssched.conf
    # ntfy is not reachable here; the page is recorded instead.
    printf '#!/bin/sh\nlogger -t nut-page "page: $1"\n' > /etc/nut/ntfy-page.sh
    printf '[cyberpower]\n    driver = dummy-ups\n    port = replay.seq\n    desc = "replay"\n' > /etc/nut/ups.conf
    printf '%s\n' "$2" > /etc/nut/replay.seq
    chown root:nut /etc/nut/*; chmod 0640 /etc/nut/*
    chmod 0750 /etc/nut/*.sh /etc/nut/upssched-cmd; chmod 0644 /etc/nut/replay.seq
    # SHUTDOWNCMD ends in /sbin/shutdown: record the call instead of halting.
    printf '#!/bin/sh\necho "$@" > %s/shutdown-called\n' "$OUT" > /sbin/shutdown
    chmod 0755 /sbin/shutdown
    "$NUTBIN/dummy-ups" -a cyberpower -u root >/dev/null 2>&1 &
    wait_for "driver up" 20 test -S /run/nut/dummy-ups-cyberpower || return 1
    upsd -u root >/dev/null 2>&1 || return 1
    wait_for "upsd up" 20 upsc "$UPS" ups.status || return 1
    upsmon >/dev/null 2>&1
    echo "-- $1"
}

wait_for() {  # wait_for <what> <timeout s> <command...>: true once command succeeds
    local what=$1 limit=$2; shift 2
    local end=$((SECONDS + limit))
    while [ "$SECONDS" -lt "$end" ]; do
        "$@" >/dev/null 2>&1 && return 0
        sleep "$SAMPLE"
    done
    echo "   timed out after ${limit}s waiting for: $what"
    return 1
}

logged() { grep -q -- "$1" "$LOG"; }
fsd_set() { upsc "$UPS" ups.status 2>/dev/null | grep -qw FSD; }
shutdown_ran() { [ -f "$OUT/shutdown-called" ]; }

pass() { echo "   ok: $1"; }
fail() { echo "   FAIL: $1"; failures=$((failures + 1)); grep -E 'nut-|upsmon|upsd' "$LOG" | tail -15 | sed 's/^/     /'; }

no_fsd_for_hold() {  # the absence check: nothing sets FSD across HOLD seconds
    if wait_for "FSD (expected none)" "$HOLD" fsd_set; then return 1; fi
    ! shutdown_ran
}

LB_ON_LINE='ups.status: OL
battery.charge: 100
TIMER 8
ups.status: OL DISCHRG LB
TIMER 3600'

# control: the rule removed in #1108 put back (it ran the same graceful
# shutdown the settle timer runs), so LB on line power must shut down.
setup control "$LB_ON_LINE" 'AT LOWBATT * EXECUTE onbatt-settle' || fail "control: setup"
wait_for "LOWBATT notify" 40 logged 'event=LOWBATT' || fail "control: no LOWBATT event"
if wait_for "FSD" 20 fsd_set; then pass "control: LB on line power shut down with the old rule"
else fail "control: the harness did not see the 08:37 shutdown"; fi

setup lb-on-line "$LB_ON_LINE" || fail "lb-on-line: setup"
wait_for "LOWBATT notify" 40 logged 'event=LOWBATT' || fail "lb-on-line: no LOWBATT event"
if no_fsd_for_hold; then pass "lb-on-line: LB on line power did not shut down"
else fail "lb-on-line: LB on line power shut down"; fi

setup ob-lb 'ups.status: OL
TIMER 8
ups.status: OB DISCHRG
TIMER 6
ups.status: OB DISCHRG LB
TIMER 3600' || fail "ob-lb: setup"
if wait_for "FSD" 40 fsd_set; then pass "ob-lb: OB+LB set FSD"; else fail "ob-lb: OB+LB did not set FSD"; fi
if wait_for "SHUTDOWNCMD" 30 shutdown_ran; then pass "ob-lb: SHUTDOWNCMD ran"; else fail "ob-lb: SHUTDOWNCMD did not run"; fi

setup settle 'ups.status: OL
TIMER 8
ups.status: OB DISCHRG
TIMER 3600' || fail "settle: setup"
wait_for "ONBATT notify" 30 logged 'event=ONBATT' || fail "settle: no ONBATT event"
if wait_for "FSD" $((SETTLE + 20)) fsd_set; then pass "settle: OB past ${SETTLE}s set FSD"
else fail "settle: OB past the settle timer did not set FSD"; fi

setup blip 'ups.status: OL
TIMER 8
ups.status: OB DISCHRG
TIMER 4
ups.status: OL
TIMER 3600' || fail "blip: setup"
wait_for "ONLINE notify" 40 logged 'event=ONLINE' || fail "blip: no ONLINE event"
HOLD=$((SETTLE + 10))
if no_fsd_for_hold; then pass "blip: back on line inside ${SETTLE}s, no FSD"
else fail "blip: a blip shorter than the settle timer shut down"; fi

stop_daemons
echo
[ "$failures" -eq 0 ] && echo "all NUT replay cases passed" || echo "$failures NUT replay check(s) failed"
exit "$failures"
