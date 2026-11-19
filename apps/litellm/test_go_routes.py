#!/usr/bin/env python3
"""Every route on the OpenCode Go base must be on forward_client_headers_to_llm_api.

Go refuses a call without an x-opencode-session header. LiteLLM forwards the
client's header only to the routes on that list, and tool_compat adds a default
one only on them, so a go route left off it fails every call (seven binks eval
routes shipped that way, #830). The
opencode-free routes use the same base and key, and carry public input only,
so none may sit on a fallback edge or an alias either way.
The NVIDIA NIM routes share one key's 40 requests/min, so their rpm must sum
under it, the NIM comment's sum must match, and no fallback edge may spill a
NIM 429 onto a paid route.
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


NIM_LIMIT = 40  # requests/min shared by every model on one NIM key (config.yaml NIM comment)


def nim_routes(text):
    """{model_name: rpm} for every route on the NIM catalog (its api_base or the nvidia_nim/ provider).
    A block ends at the next route or the next top-level key, so the last route reads no later settings."""
    routes = {}
    for block in re.split(r'^(?=  - model_name: |\S)', text, flags=re.M):
        name = re.match(r'  - model_name: "([^"]+)"', block)
        if name and re.search(r'api_base: https://integrate\.api\.nvidia\.com/|model: "?nvidia_nim/', block):
            rpm = re.search(r"^\s+rpm: (\d+)", block, re.M)
            routes[name.group(1)] = int(rpm.group(1)) if rpm else None
    return routes


def nim_comment_sum(text):
    m = re.search(r"the routes on this key sum to (\d+), under NIM's (\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


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


class NimRoutes(unittest.TestCase):
    def setUp(self):
        with open(CONFIG) as f:
            self.text = f.read()
        self.routes = nim_routes(self.text)

    def test_every_nim_route_has_an_rpm_and_they_sum_under_the_key_limit(self):
        self.assertTrue(self.routes)
        self.assertNotIn(None, self.routes.values(), self.routes)
        self.assertLess(sum(self.routes.values()), NIM_LIMIT, self.routes)

    def test_nim_comment_states_the_real_sum(self):
        self.assertEqual(nim_comment_sum(self.text), (sum(self.routes.values()), NIM_LIMIT))

    def test_no_nim_route_on_a_fallback_edge(self):
        self.assertEqual(set(self.routes) & fallback_routes(self.text), set())

    def test_nim_check_reads_a_planted_route(self):
        text = ('  - model_name: "free-x (x)"\n    litellm_params:\n'
                '      api_base: https://integrate.api.nvidia.com/v1\n      rpm: 30\n'
                '  - model_name: "fast (y)"\n    litellm_params:\n      rpm: 99\n'
                '  - model_name: "free-z (z)"\n    litellm_params:\n'
                '      api_base: https://integrate.api.nvidia.com/v1\n'
                '  - model_name: "free-n (n)"\n    litellm_params:\n      model: "nvidia_nim/n"\n      rpm: 5\n'
                'router_settings:\n  model_group_alias:\n    rpm: 7\n')
        self.assertEqual(nim_routes(text), {"free-x (x)": 30, "free-z (z)": None, "free-n (n)": 5})


if __name__ == "__main__":
    unittest.main()
