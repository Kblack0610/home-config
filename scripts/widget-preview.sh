#!/usr/bin/env bash
# Shows the phone overview widget in a browser, at the phone's size, rendered by Home Assistant
# itself through /api/template, so the preview and the phone share one renderer.
#
#   widget-preview.sh                 this checkout's phone_widget.jinja with live rows (`hub phone --json`)
#   widget-preview.sh --rows FILE     the same, with rows from FILE (a `hub phone --json` object or its rows)
#   widget-preview.sh --deployed      what the phone shows now: sensor.agent_widget's html
#   widget-preview.sh --watch         render again on every save of the jinja (inotify, no timer)
#   widget-preview.sh --no-open       write the page and print its path, do not open a browser
#
# Writes ~/.cache/phone-widget/preview.html. Needs rbw unlocked (entry "HASS token"); exits 2
# when it is not, 1 when Home Assistant rejects the template, with its error.
set -euo pipefail

HA_URL="${HA_URL:-https://hass.kblab.me}"
JINJA="$(cd "$(dirname "$0")/.." && pwd)/apps/home-assistant/config/custom_templates/phone_widget.jinja"
OUT_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/phone-widget"
OUT="$OUT_DIR/preview.html"
# The 4x4 widget on the human's phone, measured off a screenshot (1080 px wide, 2.625 px per dp):
# the box is 358 x 600 dp, text about 13 sp at 1.2 line height, centred vertically.
BOX_W=358 BOX_H=600 FONT_PX=13

mode=draft rows_file="" watch=0 open=1
while [ $# -gt 0 ]; do
  case "$1" in
    --deployed) mode=deployed ;;
    --rows) rows_file="$2"; shift ;;
    --watch) watch=1 ;;
    --no-open) open=0 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "widget-preview: unknown argument $1" >&2; exit 64 ;;
  esac
  shift
done

if ! token="$(rbw get "HASS token" 2>/dev/null)" || [ -z "$token" ]; then
  echo "widget-preview: rbw is locked or has no \"HASS token\"; run: rbw unlock" >&2
  exit 2
fi

template() {
  if [ "$mode" = deployed ]; then
    printf '%s' "{{ state_attr('sensor.agent_widget', 'html') }}"
    return
  fi
  local rows
  if [ -n "$rows_file" ]; then
    rows="$(jq -c 'if type == "object" then .rows else . end' "$rows_file")"
  else
    rows="$(hub phone --json | jq -c '.rows')"
  fi
  # The rows go in as a JSON string literal through from_json: a Jinja string takes JSON's escapes.
  cat "$JINJA"
  printf "{{ render(state_attr('sensor.system_health', 'result') or [], %s | from_json) }}" \
    "$(jq -rn --arg r "$rows" '$r | @json')"
}

render() {
  local body code html
  body="$(template | jq -Rs '{template: .}')"
  html="$(mktemp)"
  code="$(curl -sS -m 20 -o "$html" -w '%{http_code}' \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    --data-binary "$body" "$HA_URL/api/template")" || { rm -f "$html"; echo "widget-preview: $HA_URL did not answer" >&2; return 1; }
  if [ "$code" != 200 ]; then
    echo "widget-preview: Home Assistant answered $code: $(head -c 600 "$html")" >&2
    rm -f "$html"
    return 1
  fi
  mkdir -p "$OUT_DIR"
  {
    cat <<EOF
<!doctype html><meta charset="utf-8"><title>phone widget ($mode)</title>
<style>
  body { background: #6f6a8f; margin: 0; padding: 24px; font-family: Roboto, "Noto Sans", sans-serif; }
  .box { width: ${BOX_W}px; height: ${BOX_H}px; background: #2a2f3d; border-radius: 28px; color: #f1f3f4;
         font-size: ${FONT_PX}px; line-height: 1.2; display: flex; align-items: center; overflow: hidden;
         padding: 0 8px; box-sizing: border-box; outline: 1px dashed #ffffff66; outline-offset: 2px; }
  .box p { margin: 0; }
  .meta { color: #fff; font: 12px monospace; margin-top: 10px; }
</style>
<div class="box"><div>
EOF
    cat "$html"
    printf '</div></div>\n<div class="meta">%s, rendered %s; the dashed edge is where the phone clips</div>\n' \
      "$mode" "$(date +%H:%M:%S)"
  } >"$OUT"
  rm -f "$html"
}

render
echo "$OUT"
if [ "$open" = 1 ]; then xdg-open "$OUT" >/dev/null 2>&1 & fi
if [ "$watch" = 1 ]; then
  # close_write on the directory, because an editor's save often replaces the file.
  inotifywait -qm -e close_write,moved_to --format '%f' "$(dirname "$JINJA")" |
    while read -r f; do
      [ "$f" = "$(basename "$JINJA")" ] && { render || true; echo "rendered $(date +%H:%M:%S)"; }
    done
fi
