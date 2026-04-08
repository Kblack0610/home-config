#!/usr/bin/env python3
"""Regenerate the ingress-level Gatus endpoints from apps/*/ingress*.yaml.

Walks every Ingress under apps/, extracts the hostnames from spec.rules, and
rewrites the section between BEGIN_GENERATED_INGRESS / END_GENERATED_INGRESS
markers in apps/gatus/configmap.yaml. Each host becomes a generic HTTPS check
with a cert-expiration condition.

Run this after adding, renaming, or removing an Ingress. The output is
committed to git; Flux reconciles the ConfigMap and Gatus reloads.

Opt out per Ingress with:
    metadata:
      annotations:
        gatus.kblab.me/monitor: "false"
"""

import glob
import os
import re
import sys

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIGMAP = os.path.join(REPO, "apps/gatus/configmap.yaml")
INGRESS_GLOB = os.path.join(REPO, "apps/*/ingress*.yaml")
INDENT = " " * 6  # matches the indentation of entries inside data.config.yaml
BEGIN_MARK = "# BEGIN_GENERATED_INGRESS"
END_MARK = "# END_GENERATED_INGRESS"


def extract_hosts() -> list[str]:
    hosts: set[str] = set()
    for path in sorted(glob.glob(INGRESS_GLOB)):
        with open(path) as fp:
            for doc in yaml.safe_load_all(fp):
                if not doc or doc.get("kind") != "Ingress":
                    continue
                annotations = (doc.get("metadata") or {}).get("annotations") or {}
                if annotations.get("gatus.kblab.me/monitor") == "false":
                    continue
                for rule in (doc.get("spec") or {}).get("rules") or []:
                    host = rule.get("host")
                    if host:
                        hosts.add(host)
    return sorted(hosts)


def render_block(hosts: list[str]) -> str:
    lines = [
        f"{INDENT}{BEGIN_MARK} (regenerate via scripts/gen-gatus-ingress-checks.py)",
    ]
    for host in hosts:
        lines += [
            f"{INDENT}- name: {host}",
            f"{INDENT}  group: ingress",
            f'{INDENT}  url: "https://{host}"',
            f"{INDENT}  interval: 300s",
            f"{INDENT}  client:",
            f"{INDENT}    insecure: true",
            f"{INDENT}  conditions:",
            f'{INDENT}    - "[STATUS] < 500"',
            f'{INDENT}    - "[CERTIFICATE_EXPIRATION] > 720h"',
            "",
        ]
    lines.append(f"{INDENT}{END_MARK}")
    return "\n".join(lines)


def main() -> int:
    with open(CONFIGMAP) as fp:
        content = fp.read()

    hosts = extract_hosts()
    block = render_block(hosts)

    pattern = re.compile(
        re.escape(INDENT + BEGIN_MARK) + r".*?" + re.escape(END_MARK),
        re.DOTALL,
    )
    if not pattern.search(content):
        print(
            f"ERROR: markers {BEGIN_MARK!r}/{END_MARK!r} not found in {CONFIGMAP}",
            file=sys.stderr,
        )
        return 1

    new_content = pattern.sub(block, content)
    if new_content == content:
        print(f"No changes ({len(hosts)} hosts).")
        return 0

    with open(CONFIGMAP, "w") as fp:
        fp.write(new_content)
    print(f"Updated {CONFIGMAP} with {len(hosts)} ingress checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
