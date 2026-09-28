#!/bin/bash
# Self-test for reclaim-disk.sh.j2. Runs anywhere bash and perl exist; does not
# need a Mac, ansible, or the real hosts.
#
# It renders the template with sed (crude, but it keeps the test standalone) and
# runs it against a fake Unity tree. Three behaviours have already been wrong
# here and each has a case below:
#   1. a clone with a REAL Assets dir must survive (aborted the live run once)
#   2. dry-run must delete nothing
#   3. the CI guard must both fire AND get out of the way - `pgrep -q` is not
#      portable, and `pgrep -f <name>` matches the test harness's own command
#      line, so this guard has failed in both directions
#
# Usage: ./run-tests.sh   (exit 0 = all good)
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$HERE/../templates/reclaim-disk.sh.j2"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

G="$WORK/Games"
PROM="$WORK/out.prom"
failures=0

# Render through real Jinja first. The sed rendering below is close enough to
# exercise the script's logic, but it is not Jinja and will happily pass a
# template Ansible refuses: `${#array[@]}` opens a Jinja comment, which broke
# the first deploy with "Missing end of comment tag".
jinja_check() {
    /usr/bin/env python3 - "$TEMPLATE" <<'PY' || return 1
import sys
try:
    from jinja2 import Environment
except ImportError:
    src = open(sys.argv[1]).read()
    if '{#' in src and '#}' not in src:
        print("FAIL template contains an unclosed Jinja comment opener ({#)")
        sys.exit(1)
    print("  SKIP jinja2 not installed - fell back to a {# check")
    sys.exit(0)
src = open(sys.argv[1]).read()
try:
    Environment().parse(src)
except Exception as exc:
    print(f"FAIL template is not valid Jinja: {exc}")
    sys.exit(1)
print("  PASS template parses as Jinja")
PY
}

render() {
    local dry="$1" dry_num="$2"
    sed -e "s|{{ 'true' if disk_reclaim_dry_run else 'false' }}|$dry|" \
        -e "s|{{ disk_reclaim_min_age_days }}|0|" \
        -e "s|{{ disk_reclaim_simulator_threshold_mb }}|0|" \
        -e "s|{{ disk_reclaim_prom_path }}|$PROM|" \
        -e "s|({% for r in disk_reclaim_unity_roots %}\"{{ r }}\" {% endfor %})|(\"$G\")|" \
        -e "s|{{ 1 if disk_reclaim_dry_run else 0 }}|$dry_num|" \
        "$TEMPLATE" > "$WORK/reclaim.sh"
    bash -n "$WORK/reclaim.sh" || { echo "FAIL rendered script has a syntax error"; exit 1; }
    if grep -q '{{' "$WORK/reclaim.sh"; then
        echo "FAIL unsubstituted jinja left in the rendered script"
        exit 1
    fi
}

fixtures() {
    rm -rf "$G"
    mkdir -p "$G/Proj/Assets" "$G/Proj/Library" "$G/Proj/Packages"
    mkdir -p "$G/Proj_clone_0/Library" "$G/Proj_clone_1/Assets"
    # textbook ParrelSync clone: source symlinked out, only a cache of its own
    ln -s "$G/Proj/Assets" "$G/Proj_clone_0/Assets"
    ln -s "$G/Proj/Packages" "$G/Proj_clone_0/ProjectSettings"
    printf 'cache' > "$G/Proj_clone_0/Library/blob"
    # a clone holding REAL source: must never be removed
    printf 'source' > "$G/Proj_clone_1/Assets/realsource"
    printf 'cache' > "$G/Proj/Library/blob"
    printf 'source' > "$G/Proj/Assets/realsource"
    # `find -mtime +N` reads the directory's own mtime, so age the candidates
    touch -d '60 days ago' "$G/Proj_clone_0" "$G/Proj_clone_1" "$G/Proj/Library"
}

check() {
    local desc="$1" cond="$2"
    if eval "$cond"; then
        echo "  PASS $desc"
    else
        echo "  FAIL $desc"
        failures=$(( failures + 1 ))
    fi
}

echo "== template validity =="
if ! jinja_check; then
    failures=$(( failures + 1 ))
fi

echo "== prune run =="
render false 0
fixtures
bash "$WORK/reclaim.sh" >/dev/null 2>&1
check "ParrelSync clone removed"        '[[ ! -e "$G/Proj_clone_0" ]]'
check "clone with real source survived" '[[ -f "$G/Proj_clone_1/Assets/realsource" ]]'
check "stale Unity Library removed"     '[[ ! -e "$G/Proj/Library" ]]'
check "original source survived"        '[[ -f "$G/Proj/Assets/realsource" ]]'
check "bytes reported for clones"       'grep -q "unity_clones\"} [1-9]" "$PROM"'
check "bytes reported for library"      'grep -q "unity_library\"} [1-9]" "$PROM"'

echo "== dry run =="
render true 1
fixtures
bash "$WORK/reclaim.sh" >/dev/null 2>&1
check "dry-run kept the clone"   '[[ -d "$G/Proj_clone_0" ]]'
check "dry-run kept the Library" '[[ -d "$G/Proj/Library" ]]'
check "dry-run flagged itself"   'grep -q "homelab_disk_reclaim_dry_run 1" "$PROM"'

echo "== CI guard: fires while a job runs =="
render false 0
fixtures
perl -e '$0="Runner.Worker"; sleep 20' & fake=$!
sleep 1
bash "$WORK/reclaim.sh" >/dev/null 2>&1
check "guard kept the clone"  '[[ -d "$G/Proj_clone_0" ]]'
check "guard recorded itself" 'grep -q "reason=\"ci_running\"} 1" "$PROM"'
kill "$fake" 2>/dev/null
wait "$fake" 2>/dev/null

echo "== CI guard: gets out of the way when idle =="
fixtures
sleep 1
bash "$WORK/reclaim.sh" >/dev/null 2>&1
check "prunes again with no job" '[[ ! -e "$G/Proj_clone_0" ]]'
check "guard not recorded"       'grep -q "reason=\"ci_running\"} 0" "$PROM"'

echo
if [[ "$failures" -eq 0 ]]; then
    echo "all checks passed"
else
    echo "$failures check(s) failed"
fi
exit "$failures"
