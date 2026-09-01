#!/usr/bin/env python3
"""Assert every Home Assistant package file is registered with the ConfigMap generator.

THE PROBLEM THIS EXISTS FOR

apps/home-assistant/kustomization.yaml lists its config files EXPLICITLY:

    configMapGenerator:
      - name: home-assistant-managed-config
        files:
          - package_avr_yamaha.yaml=config/packages/avr_yamaha.yaml
          ...

So a file can sit in config/packages/ looking completely deployed - committed, reviewed,
merged, sitting in the tree next to files that work - while never reaching the cluster,
because nothing added the line that carries it there.

Nothing catches this. `kustomize build` succeeds: an unreferenced file is not an error,
it is simply not an input. Flux applies happily. Home Assistant starts healthy. The only
symptom is at the far end, on a wall tablet, where a dashboard button calls a script that
was never defined and does nothing when pressed.

That is exactly how apps/home-assistant/config/packages/honeywell_fan.yaml shipped in
b41a897 (2026-07-09): the package, the IR tooling, and THREE dashboard buttons landed
together, the kustomization line did not, and the branch was abandoned - plausibly
because someone pressed a button, saw nothing happen, and never found out why. It was
re-landed as broken as it was written, and only caught by querying the live HA API for
the entities it was supposed to create.

THE INVARIANT

Every *.yaml in config/packages/ is referenced by kustomization.yaml. One direction only:
a reference to a file that does not exist is already a hard `kustomize build` failure, so
it needs no check here.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
KUSTOMIZATION = ROOT / "apps/home-assistant/kustomization.yaml"
PACKAGES = ROOT / "apps/home-assistant/config/packages"


def main() -> int:
    if not KUSTOMIZATION.is_file():
        print(f"FAIL: {KUSTOMIZATION} not found")
        return 1
    if not PACKAGES.is_dir():
        print(f"FAIL: {PACKAGES} not found")
        return 1

    # Read as text, not YAML: the reference is a "key=path" string, and the path is the
    # only half that matters. Parsing the document would not make the match any surer.
    kustomization = KUSTOMIZATION.read_text()

    found = sorted(PACKAGES.glob("*.yaml"))
    # An empty package set means the layout moved and this check has silently stopped
    # checking anything. Fail rather than pass vacuously.
    if not found:
        print(f"FAIL: no package files under {PACKAGES.relative_to(ROOT)}")
        return 1

    missing = [p for p in found if f"config/packages/{p.name}" not in kustomization]

    for p in missing:
        print(f"FAIL: config/packages/{p.name} is not in kustomization.yaml")
        print(f"      add:  - package_{p.stem}.yaml=config/packages/{p.name}")
    if missing:
        print(f"\n{len(missing)} of {len(found)} package files would never reach the cluster.")
        return 1

    print(f"ok: all {len(found)} home-assistant package files are registered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
