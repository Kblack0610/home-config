#!/bin/sh
# Offline test of ci-disk-prune.sh: a stub docker and pgrep, a scratch runner home. CI runs it
# in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir "$t/bin"
fail=0
check() { # description, pattern, file, expected count
    n=$(grep -c -- "$2" "$3"); n=${n:-0}
    if [ "$n" = "$4" ]; then echo "ok   $1"; else echo "FAIL $1: $n of '$2', wanted $4"; fail=1; fi
}
old() { date -d "@$(( $(date +%s) - $1 * 86400 ))" '+%Y-%m-%d %H:%M:%S -0700 PDT'; }

{
    echo "git.kblab.me/kblack0610/unity-playground-gallery:old|$(old 2)"
    echo "git.kblab.me/kblack0610/unity-playground-gallery:new|$(date '+%Y-%m-%d %H:%M:%S -0700 PDT')"
    echo "unity-core-playground-test:old|$(old 8)"
    echo "unity-core-playground-test:new|$(old 3)"
    echo "unity-core-playground-test:inuse|$(old 9)"
    echo "d8f635:0462fbea019e4cd9b69d0cc8a3e53f7e|$(old 180)"
    echo "unityci/editor:ubuntu-6000.0.72f1-base-3|$(old 210)"
    echo "postgres:15|$(old 400)"
    echo "unity-other-test:old|$(old 30)"
    echo "alpine:3.20|$(old 180)"
    echo "ghcr.io/gitleaks/gitleaks:<none>|$(old 200)"
} > "$t/images"
cat > "$t/bin/docker" <<STUB
#!/bin/sh
case "\$1 \$2" in
    "images --format") cat "$t/images" ;;
    "image rm") echo "\$3" >> "$t/removed"; case "\$3" in *inuse) exit 1 ;; esac ;;
esac
STUB
cat > "$t/bin/pgrep" <<STUB
#!/bin/sh
case "\$2" in *unity-pool-2/*) exit 0 ;; esac; exit 1
STUB
chmod +x "$t/bin/docker" "$t/bin/pgrep"
touch "$t/removed"

R="$t/runners"; repo=_work/game/game/Builds
for tree in unity-pool-1/$repo/perf-tree/stale unity-pool-1/$repo/release-tree/stale unity-pool-1/$repo/release-tree/fresh \
            unity-pool-2/$repo/perf-tree/busy unity-pool-1/$repo/other/stale; do
    mkdir -p "$R/$tree"; touch -d '10 days ago' "$R/$tree/file"
done
touch -d '3 days ago' "$R/unity-pool-1/$repo/release-tree/fresh/recent"

RUNNER_HOME=$R PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
rc=$?

[ "$rc" = 0 ] && echo "ok   exits 0" || { echo "FAIL exit $rc"; fail=1; }
check "gallery past a day goes"          'gallery:old$' "$t/removed" 1
check "gallery under a day stays"        'gallery:new$' "$t/removed" 0
check "test image past 7 days goes"      'playground-test:old$' "$t/removed" 1
check "test image under 7 days stays"    'playground-test:new$' "$t/removed" 0
check "runner action build goes"         '^d8f635:' "$t/removed" 1
check "editor image stays"               'unityci/editor' "$t/removed" 0
check "postgres stays"                   'postgres' "$t/removed" 0
check "a similar name stays"             'unity-other' "$t/removed" 0
check "alpine stays"                     'alpine' "$t/removed" 0
check "an untagged image stays"          'gitleaks' "$t/removed" 0
check "an image in use is reported"      'kept unity-core-playground-test:inuse' "$t/log" 1
for gone in perf-tree/stale release-tree/stale; do
    if [ -e "$R/unity-pool-1/$repo/$gone" ]; then echo "FAIL $gone survived"; fail=1; else echo "ok   $gone removed"; fi
done
for kept in unity-pool-1/$repo/release-tree/fresh unity-pool-2/$repo/perf-tree/busy unity-pool-1/$repo/other/stale; do
    if [ -e "$R/$kept/file" ]; then echo "ok   kept $kept" | sed "s#$repo/##"; else echo "FAIL $kept removed"; fail=1; fi
done
exit $fail
