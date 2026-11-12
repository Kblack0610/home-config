#!/usr/bin/env python3
"""Regenerate the ingress-level Gatus endpoints from apps/*/ingress*.yaml.

Walks every Ingress under apps/, extracts the hostnames from spec.rules, and
rewrites the section between BEGIN_GENERATED_INGRESS / END_GENERATED_INGRESS
markers in apps/gatus/config.yaml. Each host becomes a generic HTTPS check
with a cert-expiration condition.

Run this after adding, renaming, or removing an Ingress. The output is
committed to git; Flux reconciles the ConfigMap and Gatus reloads.

Opt out per Ingress with:
    metadata:
      annotations:
        gatus.kblab.me/monitor: "false"

An app's Ingress names its group, the project-map tracker key, so its checks land on
that app's status page (agents.kblab.me/#apps/<key>) instead of the generic `ingress`:
    metadata:
      annotations:
        gatus.kblab.me/group: "black-dev"

`--check` exits 1 when the committed block differs from what the ingresses say.
"""

import argparse
import glob
import os
import re
import sys

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIGMAP = os.path.join(REPO, "apps/gatus/config.yaml")
INGRESS_GLOB = os.path.join(REPO, "apps/*/ingress*.yaml")
INDENT = " " * 2  # matches the indentation of entries in apps/gatus/config.yaml
GROUP_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")  # a tracker key: DNS-1123 label
BEGIN_MARK = "# BEGIN_GENERATED_INGRESS"
END_MARK = "# END_GENERATED_INGRESS"


def extract_hosts() -> dict[str, str]:
    """Each monitored host and its Gatus group."""
    hosts: dict[str, str] = {}
    for path in sorted(glob.glob(INGRESS_GLOB)):
        with open(path) as fp:
            for doc in yaml.safe_load_all(fp):
                if not doc or doc.get("kind") != "Ingress":
                    continue
                annotations = (doc.get("metadata") or {}).get("annotations") or {}
                if annotations.get("gatus.kblab.me/monitor") == "false":
                    continue
                group = annotations.get("gatus.kblab.me/group", "ingress")
                if not isinstance(group, str) or not GROUP_RE.match(group):
                    sys.exit(f"ERROR: {path}: gatus.kblab.me/group {group!r} is not a tracker key")
                for rule in (doc.get("spec") or {}).get("rules") or []:
                    host = rule.get("host")
                    if not host:
                        continue
                    if hosts.get(host, group) != group:
                        sys.exit(f"ERROR: {host} is in groups {hosts[host]!r} and {group!r}; one Ingress must own it")
                    hosts[host] = group
    return dict(sorted(hosts.items()))


def render_block(hosts: dict[str, str]) -> str:
    lines = [
        f"{INDENT}{BEGIN_MARK} (regenerate via scripts/gen-gatus-ingress-checks.py)",
    ]
    for host, group in hosts.items():
        lines += [
            f"{INDENT}- name: {host}",
            f"{INDENT}  group: {group}",
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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed block is stale")
    args = ap.parse_args()
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

    new_content = pattern.sub(lambda _: block, content)
    if new_content == content:
        print(f"No changes ({len(hosts)} hosts).")
        return 0
    if args.check:
        print(f"ERROR: {CONFIGMAP} is stale; run scripts/gen-gatus-ingress-checks.py", file=sys.stderr)
        return 1

    with open(CONFIGMAP, "w") as fp:
        fp.write(new_content)
    print(f"Updated {CONFIGMAP} with {len(hosts)} ingress checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
