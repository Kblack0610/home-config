#!/usr/bin/env bash
# Checksummed parallel downloads over the WAN, to catch the TLS bad_record_mac fault (hc-647)
# on demand instead of waiting for CI to hit it. Runs on any LAN host with curl and sha256sum.
#
#   scripts/wan-integrity-probe.sh [streams] [rounds]     defaults: 4 streams, 5 rounds
#
# Every download is a 160 MB zip from dl.google.com (the CDN of tilecast run 63) with a known
# sha256. A TLS record that fails its MAC makes curl exit 35/56 with "bad record mac" or
# "decryption failed"; a body that passed TLS but differs from the checksum would mean the
# corruption sits before TLS, at the origin. Prints one summary line:
#   <host> ok=N tls=N sum=N other=N start=<epoch> end=<epoch>
# and each failure with its epoch, to match against the router log (ansible/roles/openwrt-base/README.md).
set -uo pipefail

STREAMS="${1:-4}"
ROUNDS="${2:-5}"
URL=https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip
SHA256=7ec965280a073311c339e571cd5de778b9975026cfcbe79f2b1cdcb1e15317ee

dir="$(mktemp -d)"
trap 'rm -rf "$dir"' EXIT

one() { # stream round -> one result line on stdout
  local err="$dir/$1.$2.err" sum rc
  sum="$(curl -sS --max-time 600 "$URL" 2>"$err" | sha256sum | cut -d' ' -f1)"
  rc="${PIPESTATUS[0]}"
  if [ "$rc" -ne 0 ]; then
    if grep -qiE 'bad record mac|bad decrypt|decryption failed|decrypt error' "$err"; then
      echo "tls $(date +%s) rc=$rc $(head -c 200 "$err")"
    else
      echo "other $(date +%s) rc=$rc $(head -c 200 "$err")"
    fi
  elif [ "$sum" != "$SHA256" ]; then
    echo "sum $(date +%s) got=$sum"
  else
    echo "ok"
  fi
}

start="$(date +%s)"
for r in $(seq "$ROUNDS"); do
  for s in $(seq "$STREAMS"); do one "$s" "$r" >>"$dir/results" & done
  wait
done
end="$(date +%s)"

grep -v '^ok$' "$dir/results" | sed "s/^/$(uname -n) /"
count() { grep -c "^$1" "$dir/results"; }
echo "$(uname -n) ok=$(count ok) tls=$(count tls) sum=$(count sum) other=$(count other) start=$start end=$end"
