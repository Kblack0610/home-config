#!/usr/bin/env bash
# Shows the phone overview widget in a browser at every size in phone-widget-sizes.json, rendered
# by Home Assistant itself through /api/template, so the preview and the phone share one renderer.
#
#   widget-preview.sh                 this checkout's phone_widget.jinja with live rows (`hub phone --json`)
#   widget-preview.sh --rows FILE     the same, with rows from FILE (a `hub phone --json` object or its rows)
#   widget-preview.sh --deployed      what the phone shows now: sensor.agent_widget's html
#   widget-preview.sh --watch         render again on every save of the jinja (inotify, no timer)
#   widget-preview.sh --no-open       write the page and print its path, do not open a browser
#   widget-preview.sh --check         also lay the page out in headless Chromium: print each size's
#                                     clipped sections and write preview.png; exit 3 when a section
#                                     a size must show is cut off
#
# Writes ~/.cache/phone-widget/preview.html. Needs rbw unlocked (entry "HASS token"); exits 2
# when it is not, 1 when Home Assistant rejects the template, with its error. The emulator with the
# real Companion app is widget-sim.sh; this is the draft check, that one shows what is deployed.
set -euo pipefail

HA_URL="${HA_URL:-https://hass.kblab.me}"
JINJA="$(cd "$(dirname "$0")/.." && pwd)/apps/home-assistant/config/custom_templates/phone_widget.jinja"
OUT_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/phone-widget"
OUT="$OUT_DIR/preview.html"
SIZES="$(dirname "$0")/phone-widget-sizes.json"

mode=draft rows_file="" watch=0 open=1 check=0
while [ $# -gt 0 ]; do
  case "$1" in
    --deployed) mode=deployed ;;
    --rows) rows_file="$2"; shift ;;
    --watch) watch=1 ;;
    --no-open) open=0 ;;
    --check) check=1 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
  # One box per size, the same HTML in each. Text is top-left inside the Companion app's 4 dp
  # padding (widget_template.xml), as the human's S25 screenshot shows it.
  jq -r --rawfile html "$html" --arg mode "$mode" --arg at "$(date +%H:%M:%S)" '
    .device as $d
    | "<!doctype html><meta charset=\"utf-8\"><title>phone widget (\($mode))</title>
<style>
  body { background: #1b1d24; margin: 0; padding: 24px; font-family: Roboto, \"Noto Sans\", sans-serif;
         display: flex; flex-wrap: wrap; gap: 28px; align-items: flex-start; }
  figure { margin: 0; }
  /* A mock S25 home screen: its screen in dp, the widget where the human placed it (27 dp in,
     70 dp down, off their screenshot), page dots and a dock under it. */
  .phone { position: relative; width: \($d.width_px * 160 / $d.density | floor)px; height: \($d.height_px * 160 / $d.density | floor)px;
           border-radius: 36px; overflow: hidden; outline: 6px solid #0b0c10;
           background: radial-gradient(120% 60% at 50% 45%, #8f8ab3 0%, #6f6a8f 55%, #5b5679 100%); }
  .status { position: absolute; top: 10px; left: 22px; right: 22px; color: #fff; font-size: 14px;
            display: flex; justify-content: space-between; }
  .dots { position: absolute; bottom: 150px; width: 100%; text-align: center; color: #ffffffcc; font-size: 10px; letter-spacing: 6px; }
  .dock { position: absolute; bottom: 62px; left: 27px; right: 27px; display: flex; justify-content: space-between; }
  .dock i { width: 64px; height: 64px; border-radius: 20px; background: #ffffffd9; }
  .nav { position: absolute; bottom: 14px; width: 100%; text-align: center; color: #fff; font-size: 18px; letter-spacing: 70px; }
  .box { position: absolute; left: 27px; top: 70px; background: #2a2f3d; border-radius: 28px; color: #f1f3f4;
         font-size: \($d.text_sp)px; line-height: 1.2; overflow: hidden; padding: \($d.padding_dp)px; box-sizing: border-box; }
  .box p { margin: 0; }
  figcaption, .meta { color: #fff; font: 12px monospace; margin-top: 10px; }
  .clip { color: #ffcdd2; }
</style>
<div class=\"meta\" style=\"flex-basis: 100%\">\($mode), rendered \($at); one mock S25 home screen per widget size</div>\n"
    + (.sizes | map("<figure><div class=\"phone\"><div class=\"status\"><span>\($at[0:5])</span><span>93%</span></div><div class=\"box\" data-name=\"\(.name)\" data-must=\"\(.must_show | join(","))\" style=\"width: \(.width_dp)px; height: \(.height_dp)px\"><div>\($html)</div></div><div class=\"dots\">&#9679;&#9679;</div><div class=\"dock\"><i></i><i></i><i></i><i></i></div><div class=\"nav\">|||&#9675;&lt;</div></div><figcaption>\(.name), \(.width_dp)x\(.height_dp) dp<br><span class=\"clip\"></span></figcaption></figure>") | join("\n"))
    + "\n<pre id=\"result\" hidden></pre>
<script>
// A section is cut when its bold title line ends below the box edge. Each line of #result is
// size|cut sections|cut sections it must show.
const out = [];
for (const box of document.querySelectorAll(\".box\")) {
  const edge = box.getBoundingClientRect().bottom - \($d.padding_dp);
  const must = box.dataset.must.split(\",\");
  const cut = [...box.querySelectorAll(\"b\")]
    .filter(b => b.getBoundingClientRect().bottom > edge)
    .map(b => b.textContent.replace(/:$/, \"\").trim());
  const bad = cut.filter(t => must.includes(\"*\") || must.includes(t));
  box.closest(\"figure\").querySelector(\".clip\").textContent = cut.length ? \"cut: \" + cut.join(\", \") : \"\";
  out.push(box.dataset.name + \"|\" + cut.join(\", \") + \"|\" + bad.join(\", \"));
}
document.getElementById(\"result\").textContent = out.join(\"\\n\");
</script>"' "$SIZES" >"$OUT"
  rm -f "$html"
}

# Lays the page out in headless Chromium: prints each size's cut sections, writes preview.png, and
# returns 3 when a section a size must show is cut.
check() {
  local dom result bad=0 name cut must
  dom="$(chromium --headless --disable-gpu --virtual-time-budget=3000 --dump-dom "file://$OUT" 2>/dev/null)"
  # Only the text inside the result element: the script that fills it holds '|' too.
  result="$(tr '\n' '\r' <<<"$dom" | grep -o '<pre id="result"[^>]*>[^<]*</pre>' |
    sed 's/<[^>]*>//g' | tr '\r' '\n' | grep '|')" ||
    { echo "widget-preview: Chromium returned no layout" >&2; return 1; }
  chromium --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size=1820,1000 \
    --screenshot="$OUT_DIR/preview.png" "file://$OUT" >/dev/null 2>&1 || true
  while IFS='|' read -r name cut must; do
    if [ -n "$must" ]; then
      echo "$name: CUT, must show: $must"; bad=1
    else
      echo "$name: ok${cut:+ (cut, allowed: $cut)}"
    fi
  done <<<"$result"
  echo "$OUT_DIR/preview.png"
  return $((bad * 3))
}

render
echo "$OUT"
if [ "$check" = 1 ]; then check || exit $?; fi
if [ "$open" = 1 ]; then xdg-open "$OUT" >/dev/null 2>&1 & fi
if [ "$watch" = 1 ]; then
  # close_write on the directory, because an editor's save often replaces the file.
  inotifywait -qm -e close_write,moved_to --format '%f' "$(dirname "$JINJA")" |
    while read -r f; do
      [ "$f" = "$(basename "$JINJA")" ] && { render || true; echo "rendered $(date +%H:%M:%S)"; }
    done
fi
