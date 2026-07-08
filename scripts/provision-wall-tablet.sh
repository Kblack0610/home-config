#!/usr/bin/env bash
# Provision a stock-Android tablet (Lenovo Tab M9/M11) as a FreeKiosk Home
# Assistant wall panel + Binks voice satellite — as much as ADB allows, no tapping.
#
# FreeKiosk (com.freekiosk, MIT, github.com/RushB-fr/freekiosk) takes its ENTIRE
# config via `am start` intent extras, and its patched WebView auto-grants the
# browser mic once the OS RECORD_AUDIO permission exists — so a wall panel that
# runs the in-browser voice_satellite wake word is fully scriptable.
#
# What this does NOT / CANNOT script (printed at the end as a checklist):
#   - Enabling Developer Options + USB debugging on the tablet (one-time, on-device).
#   - The one-time HA login + assigning THIS browser to a voice_satellite ("Wall
#     Kitchen") in the Voice Satellite sidebar panel (per-browser identity).
#   - Confirming the FreeKiosk "Keep Screen On" toggle / mic survival (verify on device).
#
# Usage:
#   scripts/provision-wall-tablet.sh --name wall-kitchen [options]
#
# Options:
#   --name NAME           Panel name / MQTT device name (e.g. wall-kitchen). Required.
#   --serial SERIAL       adb device serial (auto-detected if only one is attached).
#   --url URL             Dashboard URL. Default: https://hass.kblab.me/wall-panels
#   --pin PIN             FreeKiosk settings/exit PIN. Default: 1234 (CHANGE IT).
#   --apk PATH            Install this FreeKiosk APK first (else assumes installed).
#   --mqtt-broker HOST    Enable MQTT to this broker (e.g. mosquitto.kblab.me / an IP).
#   --mqtt-user USER      MQTT username (optional).
#   --mqtt-pass PASS      MQTT password (optional).
#   --mqtt-port PORT      MQTT port. Default: 1883.
#   --rest-key KEY        Enable the REST API (port 8080) with this X-Api-Key.
#   --device-owner        Also provision as Android Device Owner (true lock-task).
#                         DESTRUCTIVE prereq: ALL accounts + SIM removed first.
#   --dry-run             Print the adb commands instead of running them.
#   -h|--help             This help.
#
# Refs: docs/wall-panels.md · docs/home-assistant-voice.md (Stage 4)
set -euo pipefail

PKG="com.freekiosk"
ACT="$PKG/.MainActivity"
DEVICE_ADMIN="$PKG/.DeviceAdminReceiver"

NAME="" SERIAL="" APK="" DEVICE_OWNER=0 DRY=0
URL="https://hass.kblab.me/wall-panels"
PIN="1234"
MQTT_BROKER="" MQTT_USER="" MQTT_PASS="" MQTT_PORT="1883" REST_KEY=""

die() { echo "error: $*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; s/^#//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2;;
    --serial) SERIAL="$2"; shift 2;;
    --url) URL="$2"; shift 2;;
    --pin) PIN="$2"; shift 2;;
    --apk) APK="$2"; shift 2;;
    --mqtt-broker) MQTT_BROKER="$2"; shift 2;;
    --mqtt-user) MQTT_USER="$2"; shift 2;;
    --mqtt-pass) MQTT_PASS="$2"; shift 2;;
    --mqtt-port) MQTT_PORT="$2"; shift 2;;
    --rest-key) REST_KEY="$2"; shift 2;;
    --device-owner) DEVICE_OWNER=1; shift;;
    --dry-run) DRY=1; shift;;
    -h|--help) usage 0;;
    *) die "unknown arg: $1 (see --help)";;
  esac
done
[ -n "$NAME" ] || { echo "--name is required"; usage 1; }
command -v adb >/dev/null || die "adb not found (see the adb-ops skill)"

# Resolve device (skip discovery in --dry-run so you can preview without a tablet)
if [ -z "$SERIAL" ] && [ "$DRY" != 1 ]; then
  mapfile -t DEVS < <(adb devices | awk 'NR>1 && $2=="device"{print $1}')
  [ "${#DEVS[@]}" -eq 1 ] || die "expected exactly 1 authorized adb device, found ${#DEVS[@]}. Use --serial. (enable Developer Options → USB debugging, plug in / adb tcpip)"
  SERIAL="${DEVS[0]}"
fi
: "${SERIAL:=DRYRUN}"
A() { if [ "$DRY" = 1 ]; then echo "adb -s $SERIAL $*"; else adb -s "$SERIAL" "$@"; fi; }

if [ "$DRY" != 1 ]; then
  MODEL="$(adb -s "$SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r' || true)"
  ANDROID="$(adb -s "$SERIAL" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r' || true)"
else
  MODEL="(dry-run)"; ANDROID="?"
fi
echo "==> Device: ${MODEL:-?} (Android ${ANDROID:-?}, serial $SERIAL)"

# 1. Install / verify FreeKiosk
if [ -n "$APK" ]; then
  echo "==> Installing FreeKiosk from $APK"
  A install -r -g "$APK"
elif [ "$DRY" != 1 ] && ! adb -s "$SERIAL" shell "pm list packages | grep -q $PKG" 2>/dev/null; then
  die "FreeKiosk ($PKG) not installed. Sideload the APK from github.com/RushB-fr/freekiosk/releases (v1.2.19+), or pass --apk PATH."
fi

# 2. OS-level grants (mic is auto-granted by FreeKiosk's WebView patch once the
#    runtime RECORD_AUDIO permission exists; the others avoid NotReadableError /
#    let FreeKiosk self-enable its accessibility + usage-stats features).
echo "==> Granting permissions (mic, audio, secure settings, usage stats)"
A shell pm grant "$PKG" android.permission.RECORD_AUDIO || true
A shell pm grant "$PKG" android.permission.MODIFY_AUDIO_SETTINGS || true
A shell pm grant "$PKG" android.permission.WRITE_SECURE_SETTINGS || true
A shell appops set "$PKG" android:get_usage_stats allow || true

# 3. Keep-awake for a mounted, always-powered panel (7 = AC|USB|WIRELESS). This
#    is the reliable keep-screen-on for a wall tablet; the WebView + mic stay live
#    only while the screen is on, so NEVER let it truly sleep.
echo "==> Keep-awake while charging + max screen timeout"
A shell settings put global stay_on_while_plugged_in 7 || true
A shell settings put system screen_off_timeout 2147483647 || true

# 4. Optional: Device Owner (true lock-task). Prereqs are destructive.
if [ "$DEVICE_OWNER" = 1 ]; then
  echo "==> Provisioning Device Owner ($DEVICE_ADMIN)"
  echo "    Prereq: ALL accounts removed (Settings → Accounts = none) + SIM out."
  read -r -p "    Continue? [y/N] " ans; [ "${ans:-N}" = "y" ] || die "aborted device-owner"
  A shell dpm set-device-owner "$DEVICE_ADMIN"
fi

# 5. Push the whole FreeKiosk config in one intent bundle.
#    screensaver disabled: a real screen-off / navigating screensaver tears down
#    getUserMedia and drops the wake-word mic — keep the dashboard foregrounded.
echo "==> Pushing FreeKiosk config (URL, PIN, auto-boot, screensaver off${REST_KEY:+, REST}${MQTT_BROKER:+, MQTT})"
EXTRAS=(
  --es pin "$PIN"
  --es url "$URL"
  --ez kiosk_enabled true
  --es auto_launch "true" --es auto_relaunch "true" --ez auto_start true
  --es screensaver_enabled "false"
)
[ -n "$REST_KEY" ] && EXTRAS+=( --es rest_api_enabled "true" --es rest_api_port "8080" --es rest_api_key "$REST_KEY" )
if [ -n "$MQTT_BROKER" ]; then
  EXTRAS+=( --es mqtt_enabled "true" --es mqtt_broker_url "$MQTT_BROKER" --es mqtt_port "$MQTT_PORT"
            --es mqtt_discovery_prefix "homeassistant" --es mqtt_device_name "$NAME" )
  [ -n "$MQTT_USER" ] && EXTRAS+=( --es mqtt_username "$MQTT_USER" )
  [ -n "$MQTT_PASS" ] && EXTRAS+=( --es mqtt_password "$MQTT_PASS" )
fi
A shell am start -n "$ACT" "${EXTRAS[@]}"

cat <<EOF

==> Scripted provisioning done for '$NAME' ($MODEL).

Now finish these ON THE TABLET / IN HA (not ADB-scriptable):
  1. HA login: when the dashboard loads, log in ONCE (as an admin so the sidebar
     shows). The WebView keeps the session.
  2. Assign the satellite: open the **Voice Satellite** sidebar panel → assign THIS
     browser → the **"Wall Kitchen"** satellite (per-browser identity, like browser_mod).
  3. FreeKiosk **Keep Screen On** toggle: confirm it's on (the ADB stay-awake covers
     charging, but flip the in-app toggle too). Leave the screensaver OFF/Dim-only —
     a screen-off or page-navigating screensaver kills the wake-word mic.
  4. CHANGE THE PIN from '$PIN' if you left the default (FreeKiosk → Settings → PIN).
  5. Verify the mic: say "ok nabu" → wake chime → a command → Binks replies. To debug,
     chrome://inspect the WebView from a desktop and watch getUserMedia.

Runtime control (if you enabled MQTT/REST): FreeKiosk auto-publishes ~42 HA entities
over MQTT discovery (screen, brightness, reload, TTS, current URL, …). See docs/wall-panels.md.
EOF
