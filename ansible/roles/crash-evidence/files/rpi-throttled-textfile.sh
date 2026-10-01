#!/bin/sh
# Export the Raspberry Pi firmware's throttle flags for node-exporter's
# textfile collector.
#
# The low bits are the state right now; bits 16-19 LATCH from boot until the
# next reboot. A brownout lasting milliseconds sets the latch even though
# nothing sampled it, so polling once a minute loses nothing - which is why a
# timer is acceptable here: the firmware exposes this only as a register to
# read, with no event to wait on.
set -eu

out_dir="${1:-/var/lib/prometheus/node-exporter}"
raw="$(vcgencmd get_throttled)"
val=$(( ${raw#*=} ))

bit() { echo $(( (val >> $1) & 1 )); }

tmp="$out_dir/.rpi_throttled.prom.$$"
{
  echo '# HELP rpi_throttled Raspberry Pi firmware throttle flags (vcgencmd get_throttled). when="since_boot" latches until reboot.'
  echo '# TYPE rpi_throttled gauge'
  for spec in undervoltage:0 freq_capped:1 throttled:2 soft_temp_limit:3; do
    flag="${spec%%:*}"; b="${spec#*:}"
    echo "rpi_throttled{flag=\"$flag\",when=\"now\"} $(bit "$b")"
    echo "rpi_throttled{flag=\"$flag\",when=\"since_boot\"} $(bit $((b + 16)))"
  done
} > "$tmp"
mv "$tmp" "$out_dir/rpi_throttled.prom"
