#!/bin/sh
# Offline test of ci-tree-prune.sh: stub pgrep, ps, lsof and df, a scratch runner home. CI runs
# it in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir "$t/bin"
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
gone() { if [ -e "$2" ]; then bad "$1: $2 is still there"; else ok "$1"; fi; }
kept() { if [ -e "$2" ]; then ok "$1"; else bad "$1: $2 was removed"; fi; }

R="$t/runner"; W="$R/_work/ucp"; C="$W/ucp"
# The runner's live job, matched the way pgrep -f does.
cat > "$t/bin/pgrep" <<'STUB'
#!/bin/sh
[ -n "${BUSY:-}" ] && echo "$BUSY/bin.2.338.2/Runner.Worker spawnclient 159 162" | grep -Eq -- "$2"
STUB
# The real ps too, since its list holds the prune's own commands: a grep that ran beside ps
# once named every tree, so every tree read as in use.
real_ps=$(command -v ps)
cat > "$t/bin/ps" <<STUB
#!/bin/sh
$real_ps "\$@"
echo "Unity -batchmode -quit -projectPath $W/ucp-unity -buildTarget OSXUniversal"
echo "git -C $W/ucp-git fetch --tags"
STUB
cat > "$t/bin/lsof" <<STUB
#!/bin/sh
printf 'p1\nn/\np2\nn$W/ucp-cwd/Assets\n'
STUB
# Free space starts at START_PCT and gains PER_TREE points for each of the TREES gone.
cat > "$t/bin/df" <<'STUB'
#!/bin/sh
n=$(ls -d "$W"/*/.git 2>/dev/null | wc -l)
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
echo "fake 100 0 $(( ${START_PCT:-50} + ${PER_TREE:-0} * (${TREES:-0} - n) )) 0% /"
STUB
chmod +x "$t/bin/"*
export PATH="$t/bin:$PATH" W

tree() { # name, age of its Library
    mkdir -p "$W/$1/Library"
    echo "gitdir: $C/.git/worktrees/$1" > "$W/$1/.git"
    touch -d "$2" "$W/$1/Library"
}
setup() {
    rm -rf "$R"; mkdir -p "$C/.git" "$C/Library"; touch -d '30 days ago' "$C/Library" "$C"
    tree ucp-stale '10 days ago'
    tree ucp-fresh '2 days ago'
    tree ucp-older '4 days ago'
    tree ucp-recent '10 minutes ago'
    tree ucp-unity '10 days ago'
    tree ucp-unity-ios '3 days ago'
    tree ucp-git '10 days ago'
    tree ucp-cwd '10 days ago'
    mkdir -p "$W/.pruned-ucp-half/Library"
}
run() { RUNNERS="$R" sh "$here/ci-tree-prune.sh" > "$t/log" 2>&1 || bad "exit $?: $(cat "$t/log")"; }

setup; run
kept "the checkout stays, whatever its age" "$C/Library"
gone "a tree unused for a week goes" "$W/ucp-stale"
gone "a half-done removal is finished" "$W/.pruned-ucp-half"
kept "a week-old tree Unity has open stays" "$W/ucp-unity"
kept "a week-old tree a git command names stays" "$W/ucp-git"
kept "a week-old tree a process works in stays" "$W/ucp-cwd"
kept "a tree used this week stays with room on the disk" "$W/ucp-fresh"

# Eight trees and the checkout. 10% free, 5 points back per tree: the week-old one gives 15,
# then idle trees go oldest first until 25.
setup; TREES=9 START_PCT=10 PER_TREE=5 run
gone "low disk: the oldest idle tree goes first" "$W/ucp-older"
gone "  then the next oldest" "$W/ucp-unity-ios"
kept "  and it stops at the target" "$W/ucp-fresh"
kept "  never the checkout" "$C/Library"
kept "  never a tree in use" "$W/ucp-unity"
setup; TREES=9 START_PCT=0 PER_TREE=1 run
gone "low disk with no target in reach: every idle tree goes" "$W/ucp-recent"
gone "  including one whose name extends a tree in use" "$W/ucp-unity-ios"
kept "  but not one in use" "$W/ucp-git"
setup; TREES=9 START_PCT=0 PER_TREE=1 BUSY="$R" run
kept "low disk on a busy runner: a tree written in the last hour stays" "$W/ucp-recent"
gone "  an older idle one goes" "$W/ucp-fresh"
setup; TREES=9 START_PCT=0 PER_TREE=1 BUSY="$t/other-runner" run
gone "another runner's job does not make this one busy" "$W/ucp-recent"

exit $fail
