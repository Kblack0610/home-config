#!/bin/sh
# Managed by ansible (unity-ci-host-mac). Keeps the build trees unity-core-playground's
# scripts/build.sh leaves beside a Mac runner's checkout from filling the disk; the rules and
# why: docs/mac-machines.md#build-tree-prune. Tests: test-ci-tree-prune.sh beside this file.
#
# A tree is in use when a process names it or works inside it, or, while the runner has a
# job, when it was written in the last IDLE_MINUTES. Only trees not in use are touched. The
# checkout, whose .git is a directory, is the job's workspace and is never touched.
set -u

RUNNERS=${RUNNERS:-"$HOME/actions-runner-playground"}
TREE_DAYS=${TREE_DAYS:-7}
FREE_PCT=${FREE_PCT:-25}
IDLE_MINUTES=${IDLE_MINUTES:-60}

now=$(date +%s)

# Unity writes Library, Logs and Temp on every open, and Builds on every build.
last_used() {
    newest=0
    for d in "$1/Library" "$1/Logs" "$1/Temp" "$1/Builds"; do
        [ -e "$d" ] || continue
        m=$(date -r "$d" +%s)
        [ "$m" -gt "$newest" ] && newest=$m
    done
    [ "$newest" -gt 0 ] || newest=$(date -r "$1" +%s)
    echo "$newest"
}

# Read fresh before each removal. A trailing space lets one pattern end a path, so
# <checkout>-shapecity never matches a line naming <checkout>-shapecity-ios. Read before the
# grep starts: in one pipeline ps lists the grep, whose arguments name every tree.
in_use() {
    procs=$(ps -axo command=; lsof -nP -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    printf '%s\n' "$procs" | sed 's/$/ /' | grep -qF -e "$1/" -e "$1 "
}

free_pct() {
    df -Pk "$1" | awk 'NR == 2 { print int($4 * 100 / $2) }'
}

# A rename is atomic, so a job that starts mid-removal finds no tree and makes a fresh one.
remove() {
    trash="${1%/*}/.pruned-${1##*/}"
    rm -rf -- "$trash"
    mv -- "$1" "$trash" || return 1
    echo "$2 $1"
    rm -rf -- "$trash"
}

idle=$(mktemp) || exit 1
trap 'rm -f "$idle"' EXIT

for runner in $RUNNERS; do
    [ -d "$runner/_work" ] || continue
    busy=
    # The runner keeps its binaries in bin.<version>, e.g. bin.2.338.2.
    pgrep -f "$runner/bin[^/]*/Runner\.Worker" >/dev/null && busy=1
    # A removal this script did not finish.
    for trash in "$runner"/_work/*/.pruned-*; do
        [ -e "$trash" ] && rm -rf -- "$trash"
    done
    for tree in "$runner"/_work/*/*; do
        [ -f "$tree/.git" ] || continue
        used=$(last_used "$tree")
        if { [ -n "$busy" ] && [ $((now - used)) -lt $((IDLE_MINUTES * 60)) ]; } || in_use "$tree"; then
            echo "busy $tree"
            continue
        fi
        if [ $((now - used)) -ge $((TREE_DAYS * 86400)) ]; then
            remove "$tree" stale
            continue
        fi
        printf '%s %s\n' "$used" "$tree" >> "$idle"
    done
done

sort -n "$idle" | while read -r _ tree; do
    [ "$(free_pct "${tree%/*}")" -lt "$FREE_PCT" ] || break
    if in_use "$tree"; then
        echo "busy $tree"
        continue
    fi
    remove "$tree" "low disk"
done
