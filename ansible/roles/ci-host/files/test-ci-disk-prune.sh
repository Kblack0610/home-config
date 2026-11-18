#!/bin/sh
# Offline test of ci-disk-prune.sh: a stub docker and pgrep, a scratch runner home. CI runs it
# in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir "$t/bin"
fail=0
# Never the Ansible temp dirs of the host running this test.
export ANSIBLE_TMP_ROOTS="$t/no-ansible"
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
# Free space falls DF_PER_TREE points with each release or HD tree left, from 100.
cat > "$t/bin/df" <<'STUB'
#!/bin/sh
n=$(ls -d "$RUNNER_HOME"/*/_work/*/*/Builds/release-tree/* "$RUNNER_HOME"/*/_work/*/*/Builds/players-tree/* "$RUNNER_HOME"/*/_work/*/*/Builds/hd-tree 2>/dev/null | wc -l)
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
c=$(find "$RUNNER_HOME" -type d \( -name players -o -name server -o -name two-process -o -name Bee -o -name BuildCache -o -name PlayerDataCache \) 2>/dev/null | wc -l)
echo "fake 100 0 $(( 100 - ${DF_PER_TREE:-0} * n - ${DF_PER_CACHE:-0} * c )) 0% /"
STUB
chmod +x "$t/bin/docker" "$t/bin/pgrep" "$t/bin/df"
touch "$t/removed"

R="$t/runners"; repo=_work/game/game/Builds
for tree in unity-pool-1/$repo/perf-tree/stale unity-pool-1/$repo/perf-tree/days3 unity-pool-1/$repo/perf-tree/today \
            unity-pool-1/$repo/release-tree/stale unity-pool-1/$repo/release-tree/fresh \
            unity-pool-1/$repo/players-tree/stale \
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

# The HD tree on three slots: pool-4's is past 7 days, pool-3's is the older copy, pool-2 is busy.
for copy in "unity-pool-1 1" "unity-pool-3 2" "unity-pool-2 5" "unity-pool-4 9"; do
    set -- $copy
    mkdir -p "$R/$1/$repo/hd-tree"; touch -d "$2 days ago" "$R/$1/$repo/hd-tree/file"
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
for gone in perf-tree/stale perf-tree/days3 release-tree/stale players-tree/stale; do
    if [ -e "$R/unity-pool-1/$repo/$gone" ]; then echo "FAIL $gone survived"; fail=1; else echo "ok   $gone removed"; fi
done
if [ -e "$R/unity-pool-1/$repo/release-tree/dup" ]; then echo "FAIL an older copy of a release tree survived"; fail=1; else echo "ok   an older copy of a release tree removed"; fi
check "an HD tree past 7 days goes by age" '^tree .*unity-pool-4/.*/hd-tree$' "$t/log" 1
for gone in unity-pool-4/$repo/hd-tree unity-pool-3/$repo/hd-tree; do
    if [ -e "$R/$gone" ]; then echo "FAIL $gone survived"; fail=1; else echo "ok   $gone removed" | sed "s#$repo/##"; fi
done
for kept in unity-pool-1/$repo/hd-tree unity-pool-2/$repo/hd-tree unity-pool-1/$repo/release-tree/fresh unity-pool-3/$repo/release-tree/dup unity-pool-2/$repo/release-tree/dup unity-pool-1/$repo/perf-tree/today unity-pool-2/$repo/perf-tree/busy unity-pool-1/$repo/other/stale; do
    if [ -e "$R/$kept/file" ]; then echo "ok   kept $kept" | sed "s#$repo/##"; else echo "FAIL $kept removed"; fail=1; fi
done

# Low disk: five warm trees at 20 points each leave 0% free. The oldest idle ones go until
# 35% is free (two, to 40%); the busy slot's tree stays though it is the oldest. d is an HD tree.
L="$t/low"; rm -f "$t/removed"; touch "$t/removed"
for copy in "unity-pool-1 release-tree/a 1" "unity-pool-1 release-tree/b 2" "unity-pool-1 release-tree/c 3" "unity-pool-3 hd-tree 4" "unity-pool-2 release-tree/e 5"; do
    set -- $copy
    mkdir -p "$L/$1/$repo/$2"; touch -d "$3 days ago" "$L/$1/$repo/$2/file"
done
RUNNER_HOME=$L BUSY_HOME=$L DF_PER_TREE=20 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
rc=$?
[ "$rc" = 0 ] && echo "ok   low disk exits 0" || { echo "FAIL low disk exit $rc"; fail=1; }
check "low disk removes two trees"       '^low disk ' "$t/log" 2
for gone in unity-pool-3/$repo/hd-tree unity-pool-1/$repo/release-tree/c; do
    if [ -e "$L/$gone" ]; then echo "FAIL low disk kept $gone"; fail=1; else echo "ok   low disk removed $gone" | sed "s#$repo/##"; fi
done
for kept in unity-pool-2/$repo/release-tree/e unity-pool-1/$repo/release-tree/b unity-pool-1/$repo/release-tree/a; do
    if [ -e "$L/$kept/file" ]; then echo "ok   low disk kept $kept" | sed "s#$repo/##"; else echo "FAIL low disk removed $kept"; fail=1; fi
done

# --before-job (a Unity slot's job-started hook): under the floor it prunes idle slots' trees,
# keeps the busy slot's (the job's own), and leaves images to the hourly run's ordinary passes.
J="$t/job"; rm -f "$t/removed"; touch "$t/removed"
for copy in "unity-pool-1 release-tree/a 1" "unity-pool-3 hd-tree 4" "unity-pool-4 release-tree/b 2" "unity-pool-2 release-tree/e 5"; do
    set -- $copy
    mkdir -p "$J/$1/$repo/$2"; touch -d "$3 days ago" "$J/$1/$repo/$2/file"
done
RUNNER_HOME=$J BUSY_HOME=$J DF_PER_TREE=25 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" --before-job > "$t/log" 2>&1
rc=$?
[ "$rc" = 0 ] && echo "ok   before-job under the floor exits 0" || { echo "FAIL before-job exit $rc"; fail=1; }
check "before-job says why it prunes"    '^free 0% < 35%' "$t/log" 1
check "before-job frees to the floor"    '^low disk ' "$t/log" 2
if [ -e "$J/unity-pool-2/$repo/release-tree/e/file" ]; then echo "ok   before-job kept the busy slot's tree"; else echo "FAIL before-job removed the busy slot's tree"; fail=1; fi
# Negative control: the same host above the floor. The hook must not touch a warm tree or an
# image, and must not spend the job's start on the age and copy passes.
rm -f "$t/removed"; touch "$t/removed"
mkdir -p "$J/unity-pool-1/$repo/release-tree/old"; touch -d '30 days ago' "$J/unity-pool-1/$repo/release-tree/old/file"
RUNNER_HOME=$J BUSY_HOME=$J DF_PER_TREE=0 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" --before-job > "$t/log" 2>&1
rc=$?
[ "$rc" = 0 ] && echo "ok   before-job above the floor exits 0" || { echo "FAIL before-job above the floor exit $rc"; fail=1; }
[ -s "$t/log" ] && { echo "FAIL before-job above the floor printed: $(head -n 1 "$t/log")"; fail=1; } || echo "ok   before-job above the floor does nothing"
[ -e "$J/unity-pool-1/$repo/release-tree/old/file" ] && echo "ok   before-job above the floor kept a 30-day tree" || { echo "FAIL before-job above the floor removed a tree"; fail=1; }
check "before-job above the floor removes no image" '.' "$t/removed" 0

# One run at a time: while another holds the lock on the runner home, a run waits. Driven by
# events, not a clock: a flock wrapper first tries the run's lock without blocking, which
# fails only if the test's lock covers it ("held"), tells the test over a FIFO that the run
# reached its lock, and then blocks on the real flock until the test releases.
real_flock=$(command -v flock) || { echo "FAIL no flock on PATH"; exit 1; }
mkdir "$t/lockbin"; mkfifo "$t/at-lock"
cat > "$t/lockbin/flock" <<STUB
#!/bin/sh
if $real_flock -n "\$@"; then state=free; else state=held; fi
mkdir "$t/signalled" 2>/dev/null && echo at-lock > "$t/at-lock"
[ \$state = free ] || $real_flock "\$@" || exit
echo \$state > "$t/got-lock"
STUB
chmod +x "$t/lockbin/flock"
exec 8<"$J"; flock 8
# 8<&-: the run must not inherit the holder's descriptor, or closing ours would not unlock.
RUNNER_HOME=$J BUSY_HOME=$J DF_PER_TREE=0 PATH="$t/lockbin:$t/bin:$PATH" sh "$here/ci-disk-prune.sh" --before-job > "$t/log" 2>&1 8<&- &
run=$!
# The timeout only guards a run that never takes the lock; a correct run signals at once.
if timeout 30 cat "$t/at-lock" > /dev/null; then
    exec 8<&-
    wait "$run"; rc=$?
    [ "$rc" = 0 ] && [ "$(cat "$t/got-lock" 2>/dev/null)" = held ] \
        && echo "ok   a second run waits for the lock" \
        || { echo "FAIL a second run did not wait (exit $rc, lock $(cat "$t/got-lock" 2>/dev/null)): $(head -n 1 "$t/log")"; fail=1; }
else
    exec 8<&-; kill "$run" 2>/dev/null; wait "$run"
    echo "FAIL a second run never took the lock: $(head -n 1 "$t/log")"; fail=1
fi

# A slot with .keep-warm-trees (the slim-tour slot): its trees outlive the age and copy rules,
# and under low disk the other slots' trees go first, the kept ones only under 30% free.
K="$t/kept"; rm -f "$t/removed"; touch "$t/removed"
for copy in "unity-pool-1 release-tree/tour 30" "unity-pool-1 hd-tree 20" "unity-pool-3 release-tree/tour 2"; do
    set -- $copy
    mkdir -p "$K/$1/$repo/$2"; touch -d "$3 days ago" "$K/$1/$repo/$2/file"
done
touch "$K/unity-pool-1/.keep-warm-trees"
RUNNER_HOME=$K BUSY_HOME=$K DF_PER_TREE=0 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
for kept in release-tree/tour hd-tree; do
    [ -e "$K/unity-pool-1/$repo/$kept/file" ] && echo "ok   kept slot keeps a 30-day $kept" || { echo "FAIL kept slot lost $kept by age or copy"; fail=1; }
done
# Three trees at 30 points: 10% free. pool-3's goes (40%, over 35): the kept ones stay.
RUNNER_HOME=$K BUSY_HOME=$K DF_PER_TREE=30 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
check "low disk takes the unkept tree first" '^low disk .*unity-pool-3/' "$t/log" 1
check "low disk keeps kept trees over 30%"  '^low disk .*unity-pool-1/' "$t/log" 0
# Two kept trees at 40 points: 20% free, under 30%. The older (release-tree/tour) goes.
RUNNER_HOME=$K BUSY_HOME=$K DF_PER_TREE=40 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
check "under 30% the oldest kept tree goes"  '^low disk .*unity-pool-1/.*release-tree/tour$' "$t/log" 1
check "and only until 30% is free"          '^low disk ' "$t/log" 1

# The main checkout refills without any alternate warm tree: downloaded players,
# generated players and Library build caches. Never remove imports, source or activation.
C="$t/caches"
for slot in unity-pool-1 unity-pool-2; do
    checkout="$C/$slot/_work/game/game"
    mkdir -p "$checkout/ProjectSettings"
    touch "$checkout/ProjectSettings/ProjectVersion.txt"
    for path in Builds/players Builds/server Builds/two-process Library/Bee Library/BuildCache Library/PlayerDataCache Library/Artifacts External/asset-library; do
        mkdir -p "$checkout/$path"; touch "$checkout/$path/file"
    done
    mkdir -p "$C/$slot/.cache/unity-core-playground-test/home"
    touch "$C/$slot/.cache/unity-core-playground-test/home/license"
done
# Six candidates per slot cost 7 points each: 16% free, so three idle outputs
# reach 37%. The busy slot retains all six and the idle Library stays warm.
RUNNER_HOME=$C BUSY_HOME=$C DF_PER_CACHE=7 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" --before-job > "$t/log" 2>&1
rc=$?
[ "$rc" = 0 ] || { echo "FAIL cache prune exit $rc"; fail=1; }
check "player outputs go before Library caches" '^low disk .*/Builds/' "$t/log" 3
check "prune stops at the floor with warm Library caches" '^low disk .*/Library/' "$t/log" 0
# Still under pressure: the three idle caches go, but never the busy slot's.
RUNNER_HOME=$C BUSY_HOME=$C DF_PER_CACHE=10 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" --before-job > "$t/log" 2>&1
check "low disk reclaims idle Library build caches" '^low disk .*/Library/' "$t/log" 3
for path in Builds/players Builds/server Builds/two-process Library/Bee Library/BuildCache Library/PlayerDataCache; do
    [ ! -e "$C/unity-pool-1/_work/game/game/$path" ] || { echo "FAIL idle $path survived"; fail=1; }
    [ -e "$C/unity-pool-2/_work/game/game/$path/file" ] || { echo "FAIL busy $path removed"; fail=1; }
done
for slot in unity-pool-1 unity-pool-2; do
    for path in Library/Artifacts External/asset-library; do
        [ -e "$C/$slot/_work/game/game/$path/file" ] || { echo "FAIL preserved $path removed"; fail=1; }
    done
    [ -e "$C/$slot/.cache/unity-core-playground-test/home/license" ] || { echo "FAIL activation removed"; fail=1; }
done
check "busy slot's build caches stay" '^low disk .*unity-pool-2/' "$t/log" 0

# An Ansible temp dir untouched for a day is an interrupted run's upload; a fresh one is a live
# task's, and other dirs beside them are not Ansible's to lose.
A="$t/ansible"
mkdir -p "$A/root/ansible-tmp-old" "$A/user/ansible-tmp-new" "$A/user/other"
touch "$A/root/ansible-tmp-old/source" "$A/user/ansible-tmp-new/source" "$A/user/other/file"
touch -d '2 days ago' "$A/root/ansible-tmp-old" "$A/user/other"
ANSIBLE_TMP_ROOTS="$A/root $A/use*" RUNNER_HOME="$t/none" PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
check "an Ansible temp dir past a day goes" '^ansible leftover .*/ansible-tmp-old$' "$t/log" 1
[ ! -e "$A/root/ansible-tmp-old" ] && echo "ok   old Ansible temp dir removed" || { echo "FAIL old Ansible temp dir survived"; fail=1; }
for kept in user/ansible-tmp-new/source user/other/file; do
    [ -e "$A/$kept" ] && echo "ok   kept $kept" || { echo "FAIL $kept removed"; fail=1; }
done

# A host with no Unity slots has nothing to measure and exits clean.
RUNNER_HOME="$t/none" DF_PER_TREE=20 PATH="$t/bin:$PATH" sh "$here/ci-disk-prune.sh" > "$t/log" 2>&1
rc=$?
[ "$rc" = 0 ] && echo "ok   no runner home exits 0" || { echo "FAIL no runner home exit $rc"; fail=1; }
exit $fail
