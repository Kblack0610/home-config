#!/usr/bin/env bash
# broadlink-learn.sh — guided IR/RF remote learning into Home Assistant via the
# Broadlink RM4. Prompts you button-by-button; you press the physical remote at
# the RM4 when told. Codes are stored in HA and can then be fired with
# remote.send_command (or wrapped into scripts / a SmartIR climate entity).
#
# Usage:
#   ./broadlink-learn.sh <device-name> <ir|rf> <command> [command ...]
#
# Examples:
#   ./broadlink-learn.sh dreo_tower_fan ir power speed_up speed_down mode oscillate timer
#   ./broadlink-learn.sh ufo_lamp       rf power           # 433 MHz lamp
#
# Env: HASS_URL (default https://hass.kblab.me), BROADLINK_ENTITY
#      (default remote.broadlink_rm4_pro). Token pulled from `rbw get "HASS token"`.
set -uo pipefail

BASE="${HASS_URL:-https://hass.kblab.me}"
ENTITY="${BROADLINK_ENTITY:-remote.broadlink_rm4_pro}"
TOKEN="$(rbw get 'HASS token' 2>/dev/null)"
[ -z "$TOKEN" ] && { echo "!! no 'HASS token' in rbw (unlock rbw / check the item name)"; exit 1; }

[ $# -lt 3 ] && { grep '^#' "$0" | sed 's/^# \?//'; exit 1; }
DEVICE="$1"; CTYPE="$2"; shift 2
case "$CTYPE" in ir|rf) ;; *) echo "!! type must be 'ir' or 'rf'"; exit 1 ;; esac

learn() {  # $1 = command name → prints HTTP code
  curl -s -m 60 -o /tmp/bl_learn_resp -w '%{http_code}' \
    -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"entity_id\":\"$ENTITY\",\"device\":\"$DEVICE\",\"command\":\"$1\",\"command_type\":\"$CTYPE\"}" \
    "$BASE/api/services/remote/learn_command"
}

echo "Learning $CTYPE codes into device '$DEVICE' via $ENTITY"
echo "Aim the remote at the RM4. The RM4's LED turns solid when it's listening."
[ "$CTYPE" = rf ] && echo "RF: HOLD the button through the frequency sweep, then TAP it once when the LED blinks again."

for cmd in "$@"; do
  while true; do
    read -rp $'\n▶ ['"$cmd"$'] — press ENTER, then press the button on the remote... '
    echo "  RM4 listening… press [$cmd] now."
    code=$(learn "$cmd")
    if [ "$code" = 200 ]; then
      echo "  ✓ captured [$cmd]"
      break
    fi
    echo "  ✗ HTTP $code — $(head -c 200 /tmp/bl_learn_resp)"
    read -rp "  retry [$cmd]? [Y/n] " r; [ "${r,,}" = n ] && break
  done
done

echo -e "\n✅ Done. Codes are stored in HA (.storage). Tell the agent \"codes learned\" and it will"
echo "   extract them, build send scripts / a SmartIR entity, and commit to git."
