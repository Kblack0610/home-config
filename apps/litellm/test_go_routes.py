#!/usr/bin/env python3
"""Every OpenCode Go route must be on forward_client_headers_to_llm_api.

Go refuses a call without the client's x-opencode-session header, and LiteLLM
forwards client headers only to the routes on that list, so a go route left
off it fails every call (seven binks eval routes shipped that way, #830).
Parsed with a regex so CI needs no PyYAML.
"""
import os
import re
import unittest

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def go_routes(text):
    return set(re.findall(r'^\s*- model_name: "(go \([^"]+\))"', text, re.M))


def forwarded(text):
    block = re.search(r"forward_client_headers_to_llm_api:\n((?:\s+- .+\n)+)", text)
    return set(re.findall(r'- "([^"]+)"', block.group(1))) if block else set()


class GoRoutesForwardHeaders(unittest.TestCase):
    def test_every_go_route_is_forwarded(self):
        text = open(CONFIG).read()
        self.assertTrue(go_routes(text))
        self.assertEqual(go_routes(text) - forwarded(text), set())

    def test_check_fails_on_a_route_left_off(self):
        text = '  - model_name: "go (x)"\n  forward_client_headers_to_llm_api:\n      - "go (y)"\n'
        self.assertEqual(go_routes(text) - forwarded(text), {"go (x)"})


if __name__ == "__main__":
    unittest.main()
