#!/bin/sh
# bark-archive.sh - copy Frigate's dog-bark audio events to the NAS as evidence.
#
# Shipped as the `bark-archive-script` ConfigMap and run hourly by the
# bark-archive CronJob. Frigate keeps a bark clip for 14 days (alert retention,
# apps/frigate/config.yml); this keeps every one, with a CSV log, under
# /archive = /mnt/nas/private/dog-barks:
#
#   YYYY-MM/barks.csv                                   one row per barking episode
#   YYYY-MM/YYYY-MM-DD_HH-MM-SS_<camera>_<label>.mp4    the clip, 5 s padding each side
#   .cursor                                             start_time of the newest archived event
#
# Times in the CSV and file names are local (TZ, America/Los_Angeles). Events
# are archived oldest first and the cursor moves after each one, so a failed run
# loses nothing: the next run starts where this one stopped. A clip that is
# already on disk is never fetched twice.
set -eu

API="${FRIGATE_API:-http://frigate.frigate.svc.cluster.local:5000/api}"
LABELS="${LABELS:-bark,yip,howl}"
PADDING="${PADDING:-5}"
OUT=/archive
CURSOR="$OUT/.cursor"
# One query covers the whole backlog. Hitting the limit means the cursor is far
# behind; refuse rather than silently skip the oldest events.
LIMIT=5000

apk add --no-cache curl jq tzdata >/dev/null

log() { echo "[$(date -Iseconds)] $*"; }

# The NAS disk, not an empty directory on the node's root fs: the hostPath is
# type Directory, and a real write proves the mount is live.
touch "$OUT/.write-probe" && rm -f "$OUT/.write-probe"

if [ -s "$CURSOR" ]; then
  after="$(cat "$CURSOR")"
else
  # First run: everything Frigate still holds (14 days of alert retention).
  after=$(( $(date +%s) - 14 * 86400 ))
  log "no cursor, starting 14 days back"
fi

events="$(mktemp)"
curl -fsS --max-time 60 \
  "$API/events?labels=$LABELS&after=$after&limit=$LIMIT&include_thumbnails=0" > "$events"
total="$(jq length "$events")"
if [ "$total" -ge "$LIMIT" ]; then
  log "ERROR: $total events after $after, at the $LIMIT limit. Not archiving a partial window."
  exit 1
fi

# Oldest first. Stop at the first event still in progress, so the cursor never
# moves past an episode that has not ended.
jq -r 'sort_by(.start_time) | .[] |
  [.id, .camera, .label, .start_time, (.end_time // "open"), .has_clip,
   ((.data.top_score // .data.score // 0) * 100 | round)] | @tsv' "$events" |
while IFS="$(printf '\t')" read -r id camera label start end has_clip score; do
  if [ "$end" = "open" ]; then
    log "event $id still in progress, stopping here"
    break
  fi
  s="${start%.*}"; e="${end%.*}"
  month="$(date -d "@$s" +%Y-%m)"
  stamp="$(date -d "@$s" +%Y-%m-%d_%H-%M-%S)"
  dir="$OUT/$month"
  csv="$dir/barks.csv"
  mkdir -p "$dir"
  [ -s "$csv" ] || echo "date,start,end,duration_s,camera,label,score_pct,clip" > "$csv"

  clip="${stamp}_${camera}_${label}.mp4"
  if [ "$has_clip" != "true" ]; then
    clip="none"
  elif [ ! -s "$dir/$clip" ]; then
    code="$(curl -sS --max-time 300 -o "$dir/$clip.part" -w '%{http_code}' \
      "$API/events/$id/clip.mp4?padding=$PADDING")"
    if [ "$code" = "200" ] && [ -s "$dir/$clip.part" ]; then
      mv "$dir/$clip.part" "$dir/$clip"
    elif [ "$code" = "404" ]; then
      # Recording already expired in Frigate: log the episode without a clip.
      rm -f "$dir/$clip.part"; clip="none"
    else
      rm -f "$dir/$clip.part"
      log "ERROR: clip for $id returned HTTP $code"
      exit 1
    fi
  fi

  echo "$(date -d "@$s" +%Y-%m-%d),$(date -d "@$s" +%H:%M:%S),$(date -d "@$e" +%H:%M:%S),$(( e - s )),$camera,$label,$score,$clip" >> "$csv"
  echo "$start" > "$CURSOR"
  log "archived $id ($camera $label, $(( e - s )) s, clip $clip)"
done

rm -f "$events"
# The mirror job refuses an empty source, and an empty archive is a valid
# state (no barks yet): the cursor marks that this job has run.
[ -s "$CURSOR" ] || echo "$after" > "$CURSOR"
log "done: $total events seen after $after"
