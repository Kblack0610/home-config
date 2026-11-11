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
# Matches the way pgrep -f does, against the command line a live runner has.
cat > "$t/bin/pgrep" <<STUB
#!/bin/sh
echo "\${BUSY_HOME:-$t/runners}/unity-pool-2/bin.2.338.0/Runner.Worker spawnclient 154 159" | grep -Eq -- "\$2"
STUB
# Free space falls DF_PER_TREE points with each release tree left, from 100.
cat > "$t/bin/df" <<'STUB'
#!/bin/sh
n=$(ls -d "$RUNNER_HOME"/*/_work/*/*/Builds/release-tree/* 2>/dev/null | wc -l)
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
echo "fake 100 0 $(( 100 - ${DF_PER_TREE:-0} * n )) 0% /"
STUB
chmod +x "$t/bin/docker" "$t/bin/pgrep" "$t/bin/df"
touch "$t/removed"

R="$t/runners"; repo=_work/game/game/Builds
for tree in unity-pool-1/$repo/perf-tree/stale unity-pool-1/$repo/perf-tree/days3 unity-pool-1/$repo/perf-tree/today \
            unity-pool-1/$repo/release-tree/stale unity-pool-1/$repo/release-tree/fresh \
            unity-pool-2/$repo/perf-tree/busy unity-pool-1/$repo/other/stale; do
    mkdir -p "$R/$tree"; touch -d '10 days ago' "$R/$tree/file"
done
touch -d '3 days ago' "$R/unity-pool-1/$repo/release-tree/fresh/recent"
touch -d '3 days ago' "$R/unity-pool-1/$repo/perf-tree/days3/file"
touch "$R/unity-pool-1/$repo/perf-tree/today/recent"
# One target warm on three slots: inside the age limit, and pool-2 is busy.
for copy in "unity-pool-1 3" "unity-pool-3 2" "unity-pool-2 4"; do
    set -- $copy
    mkdir -p "$R/$1/$repo/release-tree/dup"; touch -d "$2 days ago" "$R/$1/$repo/release-tree/dup/file"
done

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
for gone in perf-tree/stale perf-tree/days3 release-tree/stale; do
    if [ -e "$R/unity-pool-1/$repo/$gone" ]; then echo "FAIL $gone survived"; fail=1; else echo "ok   $gone removed"; fi
done
if [ -e "$R/unity-pool-1/$repo/release-tree/dup" ]; then echo "FAIL an older copy of a release tree survived"; fail=1; else echo "ok   an older copy of a release tree removed"; fi
for kept in unity-pool-1/$repo/release-tree/fresh unity-pool-3/$repo/release-tree/dup unity-pool-2/$repo/release-tree/dup unity-pool-1/$repo/perf-tree/today unity-pool-2/$repo/perf-tree/busy unity-pool-1/$repo/other/stale; do
    if [ -e "$R/$kept/file" ]; then echo "ok   kept $kept" | sed "s#$repo/##"; else echo "FAIL $kept removed"; fail=1; fi
done

# Low disk: five warm trees at 20 points each leave 0% free. The oldest idle ones go until
# 35% is free (two, to 40%); the busy slot's tree stays though it is the oldest.
L="$t/low"; rm -f "$t/removed"; touch "$t/removed"
for copy in "unity-pool-1 a 1" "unity-pool-1 b 2" "unity-pool-1 c 3" "unity-pool-3 d 4" "unity-pool-2 e 5"; do
    set -- $copy
    mkdir -p "$L/$1/$repo/release-tree/$2"; touch -d "$3 days ago" "$L/$1/$repo/release-tree/$2/file"
done
RUNNER_HOME=$L BUSY_HOME=$L DF_PER_TREE=20 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
rc=$?
[ "$rc" = 0 ] && echo "ok   low disk exits 0" || { echo "FAIL low disk exit $rc"; fail=1; }
check "low disk removes two trees"       '^low disk ' "$t/log" 2
for gone in unity-pool-3/$repo/release-tree/d unity-pool-1/$repo/release-tree/c; do
    if [ -e "$L/$gone" ]; then echo "FAIL low disk kept $gone"; fail=1; else echo "ok   low disk removed $gone" | sed "s#$repo/##"; fi
done
for kept in unity-pool-2/$repo/release-tree/e unity-pool-1/$repo/release-tree/b unity-pool-1/$repo/release-tree/a; do
    if [ -e "$L/$kept/file" ]; then echo "ok   low disk kept $kept" | sed "s#$repo/##"; else echo "FAIL low disk removed $kept"; fail=1; fi
done

# A host with no Unity slots has nothing to measure and exits clean.
RUNNER_HOME="$t/none" DF_PER_TREE=20 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
rc=$?
[ "$rc" = 0 ] && echo "ok   no runner home exits 0" || { echo "FAIL no runner home exit $rc"; fail=1; }
exit $fail
