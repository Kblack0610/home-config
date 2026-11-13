#!/usr/bin/env python3
"""Every route on the OpenCode Go base must be on forward_client_headers_to_llm_api.

Go refuses a call without an x-opencode-session header. LiteLLM forwards the
client's header only to the routes on that list, and tool_compat adds a default
one only on them, so a go route left off it fails every call (seven binks eval
routes shipped that way, #830). The
opencode-free routes use the same base and key, and carry public input only,
so none may sit on a fallback edge or an alias either way.
Parsed with a regex so CI needs no PyYAML.
"""
import os
import re
import unittest

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


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


class GoRoutesForwardHeaders(unittest.TestCase):
    def test_every_go_route_is_forwarded(self):
        text = open(CONFIG).read()
        self.assertTrue(go_routes(text))
        self.assertEqual(go_routes(text) - forwarded(text), set())

    def test_check_fails_on_a_route_left_off(self):
        text = '  - model_name: "go (x)"\n  forward_client_headers_to_llm_api:\n      - "go (y)"\n'
        self.assertEqual(go_routes(text) - forwarded(text), {"go (x)"})

    def test_no_opencode_free_route_on_a_fallback_edge(self):
        with open(CONFIG) as f:
            text = f.read()
        free = {r for r in go_routes(text) if r.startswith("opencode-free")}
        self.assertTrue(free)
        self.assertTrue(fallback_routes(text))
        self.assertEqual(free & fallback_routes(text), set())

    def test_fallback_check_fails_on_a_planted_edge(self):
        for text in ('  fallbacks:\n    - {"opencode-free (x)": ["fast (y)"]}\n',
                     '  context_window_fallbacks:\n    - {"fast (y)": ["opencode-free (x)"]}\n',
                     '  model_group_alias:\n    free: "opencode-free (x)"\n'):
            self.assertEqual(fallback_routes(text) & {"opencode-free (x)"}, {"opencode-free (x)"}, text)


if __name__ == "__main__":
    unittest.main()
