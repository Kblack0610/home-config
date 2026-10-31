#!/bin/sh
# Managed by ansible (platform-ci-host). What CI leaves on a persistent host that a job
# rebuilds if it wants it again. Tests: test-ci-disk-prune.sh beside this file.
#
# Never `image prune -a`: a Unity slot needs the unityci editor images, and pulling those
# on the LAN fails (roles/unity-ci-host). Names are listed here on purpose, so an image
# nobody listed stays.
set -u

RUNNER_HOME=${RUNNER_HOME:-/var/lib/actions-runner}
GALLERY_DAYS=${GALLERY_DAYS:-1}
IMAGE_DAYS=${IMAGE_DAYS:-7}
TREE_DAYS=${TREE_DAYS:-7}

now=$(date +%s)

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

# A release or perf tree holds a whole checkout and Library, 40G to 60G. perf-round.yml
# removes its own now; a tree nothing wrote to in TREE_DAYS is a slot that stopped getting
# that job. The next one there starts cold.
for work in "$RUNNER_HOME"/unity-pool-*/_work; do
    [ -d "$work" ] || continue
    slot=${work%/_work}
    if pgrep -f "$slot/bin/Runner.Worker" >/dev/null; then
        echo "busy $slot"
        continue
    fi
    for tree in "$work"/*/*/Builds/perf-tree/* "$work"/*/*/Builds/release-tree/*; do
        [ -d "$tree" ] || continue
        [ -z "$(find "$tree" -type f -mtime -"$TREE_DAYS" | head -n 1)" ] || continue
        echo "tree $tree"
        rm -rf -- "$tree"
    done
done
