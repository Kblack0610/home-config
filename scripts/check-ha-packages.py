#!/usr/bin/env python3
"""Assert every Home Assistant package file actually reaches /config/packages in the pod.

THE PROBLEM THIS EXISTS FOR

Getting one package file to Home Assistant takes THREE agreeing edits in three files:

  1. apps/home-assistant/config/packages/<name>.yaml    - the package itself
  2. apps/home-assistant/kustomization.yaml             - an explicit configMapGenerator
                                                          entry, or it is not in the ConfigMap
  3. apps/home-assistant/deployment.yaml                - an explicit `cp` line in the init
                                                          container, or it is in the ConfigMap
                                                          but never lands in /config/packages

Miss either 2 or 3 and the file looks completely deployed - committed, reviewed, merged,
sitting in the tree beside files that work - while doing nothing.

Nothing catches it. `kustomize build` succeeds: an unreferenced file is not an error, it
is simply not an input. Flux applies. Home Assistant starts healthy. The only symptom is
at the far end, on a wall tablet, where a button calls a script that was never defined.

That is how honeywell_fan.yaml shipped in b41a897 (2026-07-09): the package, the IR
tooling, and THREE dashboard buttons landed together while BOTH wiring edits did not, and
the branch was abandoned - plausibly because someone pressed a button, saw nothing, and
never found out why. Re-landing it in #210 reproduced the bug exactly. Fixing only edit 2
(#215) was not enough, and the live HA API still reported zero honeywell entities: the
two omissions are independent and each one alone is sufficient to break it.

THE INVARIANT

Every *.yaml in config/packages/ appears in BOTH the kustomization and the init
container's copy list. One direction only: a reference to a file that does not exist is
already a hard `kustomize build` failure (2) or an init-container crash (3), so the
reverse needs no check here.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
KUSTOMIZATION = ROOT / "apps/home-assistant/kustomization.yaml"
DEPLOYMENT = ROOT / "apps/home-assistant/deployment.yaml"
PACKAGES = ROOT / "apps/home-assistant/config/packages"


def main() -> int:
    for path in (KUSTOMIZATION, DEPLOYMENT):
        if not path.is_file():
            print(f"FAIL: {path.relative_to(ROOT)} not found")
            return 1
    if not PACKAGES.is_dir():
        print(f"FAIL: {PACKAGES.relative_to(ROOT)} not found")
        return 1

    # Read as text, not YAML: both references are substrings inside a "key=path" entry
    # and a shell `cp` line. Parsing the documents would not make the match any surer.
    kustomization = KUSTOMIZATION.read_text()
    deployment = DEPLOYMENT.read_text()

    found = sorted(PACKAGES.glob("*.yaml"))
    # An empty package set means the layout moved and this check has silently stopped
    # checking anything. Fail rather than pass vacuously.
    if not found:
        print(f"FAIL: no package files under {PACKAGES.relative_to(ROOT)}")
        return 1

    broken = 0
    for p in found:
        in_configmap = f"config/packages/{p.name}" in kustomization
        # The init container copies the flattened ConfigMap key to its real name.
        in_copy = f"/config/packages/{p.name}" in deployment
        if in_configmap and in_copy:
            continue
        broken += 1
        print(f"FAIL: config/packages/{p.name} never reaches /config/packages in the pod")
        if not in_configmap:
            print(f"      kustomization.yaml needs:  - package_{p.stem}.yaml=config/packages/{p.name}")
        if not in_copy:
            print(f"      deployment.yaml needs:     cp /managed-config/package_{p.stem}.yaml /config/packages/{p.name}")

    if broken:
        print(f"\n{broken} of {len(found)} package files are wired up incompletely.")
        return 1

    print(f"ok: all {len(found)} home-assistant package files are in the ConfigMap and copied into place")
    return 0


if __name__ == "__main__":
    sys.exit(main())
