#!/bin/sh
# Managed by ansible (linux-ci-host). What CI leaves on a persistent host that a job
# rebuilds if it wants it again. Tests: test-ci-disk-prune.sh beside this file.
#
# Never `image prune -a`: a Unity slot needs the unityci editor images, and pulling those
# on the LAN fails (roles/unity-ci-host). Names are listed here on purpose, so an image
# nobody listed stays.
set -u

RUNNER_HOME=${RUNNER_HOME:-/var/lib/actions-runner}
GALLERY_DAYS=${GALLERY_DAYS:-1}
IMAGE_DAYS=${IMAGE_DAYS:-7}
PERF_TREE_DAYS=${PERF_TREE_DAYS:-1}
RELEASE_TREE_DAYS=${RELEASE_TREE_DAYS:-7}
# The governor re-admits a withheld Unity slot at 25% free and withholds under 20%
# (apps/ci-runner-governor/config.json), and one release build writes up to about 45G of
# tree and output, about 10% of razer's 938G. Under 35% free the oldest idle warm trees
# (release and HD) go, whatever their age: on 2026-11-10 razer held 302G of trees, each one
# copy and under 7 days old, so the age and copy rules removed nothing while the governor
# withheld both slots.
RELEASE_TREE_MIN_FREE_PCT=${RELEASE_TREE_MIN_FREE_PCT:-35}

now=$(date +%s)
# The runner keeps its binaries in bin.<version>, e.g. bin.2.338.0.
busy() { pgrep -f "$1/bin[^/]*/Runner\.Worker" >/dev/null; }
newest() { find "$1" -type f -exec stat -c %Y {} + | sort -n | tail -n 1; }
# Warm trees a job keeps on purpose: a release tree per target, and the one HD tree hd.yml keeps
# beside a slot's checkout (26G each; on 2026-11-11 two of them held aws-burst-1 at 100%).
warm_trees() {
    for tree in "$RUNNER_HOME"/unity-pool-*/_work/*/*/Builds/release-tree/* \
                "$RUNNER_HOME"/unity-pool-*/_work/*/*/Builds/hd-tree; do
        [ -d "$tree" ] && printf '%s\n' "$tree"
    done
}
free_pct() { df -P "$RUNNER_HOME" | awk 'NR == 2 { printf "%d", $4 * 100 / $2 }'; }

# One run at a time: the hourly timer and each Unity slot's job-started hook may overlap, and
# two low-disk passes would each remove a tree for the same shortfall.
if [ -d "$RUNNER_HOME" ]; then
    exec 9<"$RUNNER_HOME"
    flock 9
fi
# --before-job: a Unity slot's job-started hook (unity-job-started.sh). It prunes only under the
# floor. The hourly run skips busy slots and nothing else held a full host back: on 2026-11-11
# aws-burst-1 sat at 0 bytes free for 30 minutes after #792, and every job there died before
# its first test. The governor cannot withhold it, since Prometheus has no node_exporter
# reading for aws-burst-1.
if [ "${1:-}" = --before-job ]; then
    [ -d "$RUNNER_HOME" ] || exit 0
    free=$(free_pct)
    if [ -z "$free" ] || [ "$free" -ge "$RELEASE_TREE_MIN_FREE_PCT" ]; then
        exit 0
    fi
    echo "free ${free}% < ${RELEASE_TREE_MIN_FREE_PCT}%: pruning idle slots before this job"
fi

# `docker image rm` without -f refuses an image a container uses.
docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | while IFS='|' read -r ref created; do
    case "$ref" in
        # A gallery image is pushed to the registry the moment it is built.
        */unity-playground-gallery:*) days=$GALLERY_DAYS ;;
        # Built from the editor image by in-docker.sh when the tag is missing.
        unity-core-playground-test:*) days=$IMAGE_DAYS ;;
        *) if printf '%s\n' "$ref" | grep -Eq '^[0-9a-f]{6}:[0-9a-f]{32}$'; then
               # The runner's build of a Dockerfile action, one per job and rebuilt each time.
               days=$IMAGE_DAYS
           else
               continue
           fi ;;
    esac
    # The first 19 characters are local date and time; the zone cannot matter at this scale.
    made=$(date -d "$(printf '%s' "$created" | cut -c1-19)" +%s) || continue
    if [ $(( (now - made) / 86400 )) -ge "$days" ]; then
        echo "image $ref"
        docker image rm "$ref" >/dev/null || echo "kept $ref: in use"
    fi
done

# A release, HD or perf tree holds a whole checkout and Library, 25G to 60G. perf-round.yml
# removes its own when it ends, so one still here after a day was left by a failed job. A
# release or HD tree is kept warm on purpose, until a slot has stopped getting that job; the
# next one there starts cold.
for work in "$RUNNER_HOME"/unity-pool-*/_work; do
    [ -d "$work" ] || continue
    slot=${work%/_work}
    if busy "$slot"; then
        echo "busy $slot"
        continue
    fi
    for tree in "$work"/*/*/Builds/perf-tree/* "$work"/*/*/Builds/release-tree/* "$work"/*/*/Builds/hd-tree; do
        [ -d "$tree" ] || continue
        case "$tree" in
            */perf-tree/*) days=$PERF_TREE_DAYS ;;
            *) days=$RELEASE_TREE_DAYS ;;
        esac
        [ -z "$(find "$tree" -type f -mtime -"$days" -print -quit)" ] || continue
        echo "tree $tree"
        rm -rf -- "$tree"
    done
done

# Any slot may get a target's next build or HD run, so one warm copy per host is all that
# pays. On 2026-11-01 older copies held razer under the governor's 25% re-admit line, with 4 of its 5
# slots idle and jobs waiting. A copy on a busy slot stays, whatever its age.
tab=$(printf '\t')
warm_trees | while read -r tree; do
    t=$(newest "$tree")
    printf '%s\t%s\t%s\n' "${tree#"$RUNNER_HOME"/*/}" "${t:-0}" "$tree"
done | sort -t "$tab" -k1,1 -k2,2nr | {
    seen=
    while IFS="$tab" read -r target _ tree; do
        if [ "$target" != "$seen" ]; then
            seen=$target
            continue
        fi
        slot=${tree%%/_work/*}
        if busy "$slot"; then
            echo "busy $slot"
            continue
        fi
        echo "older copy $tree"
        rm -rf -- "$tree"
    done
}

# A busy slot's trees stay: its job may be about to use one. The next job to start on the host
# (--before-job) or the next hourly run frees them once that slot is idle.
# A light/heavy-only host has no Unity slots, so nothing here to measure.
[ -d "$RUNNER_HOME" ] || exit 0
free=$(free_pct)
[ -n "$free" ] || { echo "no free space reading for $RUNNER_HOME"; exit 1; }
[ "$free" -lt "$RELEASE_TREE_MIN_FREE_PCT" ] || exit 0
warm_trees | while read -r tree; do
    busy "${tree%%/_work/*}" && continue
    t=$(newest "$tree")
    printf '%s\t%s\n' "${t:-0}" "$tree"
done | sort -n | while IFS="$tab" read -r _ tree; do
    [ "$(free_pct)" -lt "$RELEASE_TREE_MIN_FREE_PCT" ] || break
    echo "low disk $tree"
    rm -rf -- "$tree"
    # btrfs reports freed space only after a commit; without this df lags and more trees go.
    sync -f "$RUNNER_HOME"
done
