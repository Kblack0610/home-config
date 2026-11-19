#!/usr/bin/env python3
"""Static admission gate for config.yaml routes.

Every route on the OpenCode Go base must be on forward_client_headers_to_llm_api.
Go refuses a call without an x-opencode-session header. LiteLLM forwards the
client's header only to the routes on that list, and tool_compat adds a default
one only on them, so a go route left off it fails every call (seven binks eval
routes shipped that way, #830).

Every route tagged `free` (a third-party free tier) must be priced 0, carry an
rpm, name its key in a dated `# key-limit <ENV>: rpm N (...)` or
`# key-limit <ENV>: none published (...)` comment, carry a data-class tag, and
sit on no fallback edge or alias, so a free-tier refusal fails the call instead
of spilling onto a paid route and no caller reaches it by another name. The
rpm of every route on a key with a published limit must sum under that limit.
Steps to add a provider: README.md, "Add a provider".
Parsed with a regex so CI needs no PyYAML.
"""
import os
import re
import unittest

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

# What a free tier may receive: public-input-only (the provider may train on
# prompts) or fleet-trial (evaluation terms: fleet trial work, never client or personal data).
DATA_CLASSES = {"public-input-only", "fleet-trial"}

# Free routes that predate the gate and fail it, each with its board row.
# The gate fails when one of them starts passing, so the list only shrinks.
KNOWN_VIOLATIONS = {
    # GEMINI_API_KEY billing and mem0's personal data on it are undecided (hc-181 follow-up from hc-657).
    "embedding (gemini-001)",
}


def go_routes(text):
    return set(re.findall(r'^\s*- model_name: "((?:go|opencode-free) \([^"]+\))"', text, re.M))


def forwarded(text):
    block = re.search(r"forward_client_headers_to_llm_api:\n((?:\s+- .+\n)+)", text)
    return set(re.findall(r'- "([^"]+)"', block.group(1))) if block else set()


def fallback_routes(text):
    """Every route a router edge names: any *fallbacks list (fallbacks, default_, context_window_,
    content_policy_) and model_group_alias, through which a caller reaches a route by another name."""
    blocks = re.findall(r"^\s+(?:\w*fallbacks|model_group_alias):\n((?:    .*\n|\s*#.*\n)+)", text, re.M)
    return {r for b in blocks for r in re.findall(r'"([^"]+)"', b)}


def routes(text):
    """{model_name: {key, rpm, cost0, tags}} per route. A block ends at the next route or the next
    top-level key, so the last route reads no later settings."""
    out = {}
    for block in re.split(r'^(?=  - model_name: |\S)', text, flags=re.M):
        name = re.match(r'  - model_name: "([^"]+)"', block)
        if not name:
            continue
        key = re.search(r"^\s+api_key: os\.environ/(\w+)", block, re.M)
        rpm = re.search(r"^\s+rpm: (\d+)", block, re.M)
        tags = re.search(r"^\s+tags: \[([^\]]*)\]", block, re.M)
        out[name.group(1)] = {
            "key": key.group(1) if key else None,
            "rpm": int(rpm.group(1)) if rpm else None,
            "cost0": all(re.search(rf"^\s+{side}_cost_per_token: 0\s*$", block, re.M) for side in ("input", "output")),
            "tags": set(re.findall(r'"([^"]+)"', tags.group(1))) if tags else set(),
        }
    return out


def key_limits(text):
    """{ENV: rpm or None} from `# key-limit ENV: rpm N (source, read YYYY-MM-DD)` lines; None is
    `none published`. A line with no read date does not count."""
    out = {}
    for env, rest in re.findall(r"^\s*# key-limit (\w+): (.*)$", text, re.M):
        if not re.search(r"read \d{4}-\d{2}-\d{2}", rest):
            continue
        rpm = re.match(r"rpm (\d+)\b", rest)
        if rpm or rest.startswith("none published"):
            out[env] = int(rpm.group(1)) if rpm else None
    return out


def free_route_violations(text):
    """{route: [reason, ...]} for every free-tagged route that breaks an invariant."""
    all_routes, limits, edges = routes(text), key_limits(text), fallback_routes(text)
    bad = {}
    for name, r in all_routes.items():
        if "free" not in r["tags"]:
            continue
        why = []
        if not r["cost0"]:
            why.append("not priced 0 (input_cost_per_token and output_cost_per_token)")
        if r["rpm"] is None:
            why.append("no rpm")
        if r["key"] not in limits:
            why.append(f"no dated key-limit comment for {r['key']}")
        if not r["tags"] & DATA_CLASSES:
            why.append(f"no data-class tag ({', '.join(sorted(DATA_CLASSES))})")
        if name in edges:
            why.append("on a fallback edge or alias")
        limit = limits.get(r["key"])
        if limit is not None:
            on_key = {n: x["rpm"] or 0 for n, x in all_routes.items() if x["key"] == r["key"]}
            if sum(on_key.values()) >= limit:
                why.append(f"routes on {r['key']} sum to rpm {sum(on_key.values())}, not under its {limit}")
        if why:
            bad[name] = why
    return bad


ROUTE = ('  - model_name: "{name}"\n    litellm_params:\n      api_key: os.environ/{key}\n'
         '      input_cost_per_token: 0\n      output_cost_per_token: 0\n      rpm: {rpm}\n'
         '    model_info:\n      tags: ["free", "public-input-only"]\n')


def fixture(*route_lines, limit="rpm 40 (docs, read 2026-11-18)", tail=""):
    return f"model_list:\n  # key-limit K: {limit}\n" + "".join(route_lines) + "router_settings:\n" + tail


class GoRoutesForwardHeaders(unittest.TestCase):
    def test_every_go_route_is_forwarded(self):
        with open(CONFIG) as f:
            text = f.read()
        self.assertTrue(go_routes(text))
        self.assertEqual(go_routes(text) - forwarded(text), set())

    def test_check_fails_on_a_route_left_off(self):
        text = '  - model_name: "go (x)"\n  forward_client_headers_to_llm_api:\n      - "go (y)"\n'
        self.assertEqual(go_routes(text) - forwarded(text), {"go (x)"})


class FreeRouteGate(unittest.TestCase):
    def setUp(self):
        with open(CONFIG) as f:
            self.text = f.read()

    def test_every_free_route_passes_the_gate(self):
        free = {n for n, r in routes(self.text).items() if "free" in r["tags"]}
        self.assertTrue(free - KNOWN_VIOLATIONS)
        bad = {n: w for n, w in free_route_violations(self.text).items() if n not in KNOWN_VIOLATIONS}
        self.assertEqual(bad, {})

    def test_known_violations_still_fail(self):
        # One that passes now leaves the list, so the quarantine cannot hide a fixed route.
        self.assertEqual(KNOWN_VIOLATIONS - set(free_route_violations(self.text)), set())

    def test_a_clean_fixture_passes(self):
        self.assertEqual(free_route_violations(fixture(ROUTE.format(name="free-a (a)", key="K", rpm=15))), {})

    def test_gate_fails_on_a_planted_fallback_edge(self):
        for tail in ('  fallbacks:\n    - {"free-a (a)": ["fast (y)"]}\n',
                     '  context_window_fallbacks:\n    - {"fast (y)": ["free-a (a)"]}\n',
                     '  model_group_alias:\n    free: "free-a (a)"\n'):
            bad = free_route_violations(fixture(ROUTE.format(name="free-a (a)", key="K", rpm=15), tail=tail))
            self.assertEqual(bad, {"free-a (a)": ["on a fallback edge or alias"]}, tail)

    def test_gate_fails_on_two_routes_past_their_key_limit(self):
        bad = free_route_violations(fixture(ROUTE.format(name="free-a (a)", key="K", rpm=25),
                                            ROUTE.format(name="free-b (b)", key="K", rpm=15)))
        self.assertEqual(set(bad), {"free-a (a)", "free-b (b)"})
        self.assertIn("routes on K sum to rpm 40, not under its 40", bad["free-a (a)"])

    def test_paid_routes_on_a_shared_key_count_toward_its_limit(self):
        paid = ROUTE.format(name="paid (p)", key="K", rpm=30).replace('"free", "public-input-only"', '"paid"')
        bad = free_route_violations(fixture(ROUTE.format(name="free-a (a)", key="K", rpm=15), paid))
        self.assertEqual(list(bad), ["free-a (a)"])

    def test_none_published_skips_the_sum_but_still_needs_the_line(self):
        route = ROUTE.format(name="free-a (a)", key="K", rpm=500)
        self.assertEqual(free_route_violations(fixture(route, limit="none published (docs, read 2026-11-18)")), {})
        self.assertEqual(free_route_violations(fixture(route, limit="rpm 900 (docs, no date)")),
                         {"free-a (a)": ["no dated key-limit comment for K"]})

    def test_gate_names_each_missing_field(self):
        bare = '  - model_name: "free-a (a)"\n    litellm_params:\n      api_key: os.environ/K\n    model_info:\n      tags: ["free"]\n'
        self.assertEqual(free_route_violations(fixture(bare))["free-a (a)"], [
            "not priced 0 (input_cost_per_token and output_cost_per_token)",
            "no rpm",
            "no data-class tag (fleet-trial, public-input-only)",
        ])

    def test_route_blocks_end_at_the_next_route_and_top_level_key(self):
        text = ('  - model_name: "free-x (x)"\n    litellm_params:\n      api_key: os.environ/N\n      rpm: 30\n'
                '  - model_name: "fast (y)"\n    litellm_params:\n      rpm: 99\n'
                '  - model_name: "free-z (z)"\n    litellm_params:\n      api_key: os.environ/N\n'
                'router_settings:\n  model_group_alias:\n    rpm: 7\n')
        got = {n: (r["key"], r["rpm"]) for n, r in routes(text).items()}
        self.assertEqual(got, {"free-x (x)": ("N", 30), "fast (y)": (None, 99), "free-z (z)": ("N", None)})


if __name__ == "__main__":
    unittest.main()
