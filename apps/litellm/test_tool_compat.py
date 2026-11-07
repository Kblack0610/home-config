#!/usr/bin/env python3
"""Offline tests for tool_compat.py: LiteLLM is stubbed, so CI needs no pip step.

Covers the response_format name strip: which routes lose the name, that the
schema and strict survive, and that the caller's dict keeps the name for a
fallback onto a route that requires it.
"""
import asyncio
import os
import sys
import types
import unittest

stub = types.ModuleType("litellm.integrations.custom_logger")
stub.CustomLogger = object
sys.modules.update({
    "litellm": types.ModuleType("litellm"),
    "litellm.integrations": types.ModuleType("litellm.integrations"),
    "litellm.integrations.custom_logger": stub,
})
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tool_compat  # noqa: E402

LAZER = "https://proxy.lazertechnologies.com/v1"
SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}}


def named_format():
    return {
        "type": "json_schema",
        "json_schema": {"name": "structured_output", "schema": SCHEMA, "strict": True},
    }


def run_hook(kwargs):
    hook = tool_compat.proxy_handler_instance.async_pre_call_deployment_hook
    return asyncio.run(hook(dict(kwargs), None))


class ResponseFormatName(unittest.TestCase):
    def test_lazer_claude_route_loses_only_the_name(self):
        # /v1/chat/completions keeps the provider prefix, the /v1/messages bridge drops it.
        for model in ("openai/claude-haiku-5.5", "claude-haiku-5.5", "claude-sonnet-5.5", "claude-opus-5.5"):
            out = run_hook({"model": model, "api_base": LAZER, "response_format": named_format()})
            self.assertEqual(
                out["response_format"],
                {"type": "json_schema", "json_schema": {"schema": SCHEMA, "strict": True}},
            )

    def test_other_routes_keep_the_name(self):
        for model, api_base in (
            ("openai/gpt-oss-120b", LAZER),
            ("gemini/gemini-3-flash", None),
            ("anthropic/claude-sonnet-5", None),
            ("openai/claude-sonnet-5", "https://other.example/v1"),
        ):
            out = run_hook({"model": model, "api_base": api_base, "response_format": named_format()})
            self.assertEqual(out["response_format"], named_format())

    def test_the_callers_dict_keeps_the_name_for_a_fallback(self):
        original = named_format()
        run_hook({"model": "claude-haiku-5.5", "api_base": LAZER, "response_format": original})
        self.assertEqual(original, named_format())

    def test_requests_without_a_json_schema_pass_through(self):
        for kwargs in (
            {"model": "claude-haiku-5.5", "api_base": LAZER},
            {"model": "claude-haiku-5.5", "api_base": LAZER, "response_format": {"type": "json_object"}},
            {"model": "claude-haiku-5.5", "api_base": LAZER, "response_format": None},
            {"api_base": LAZER, "response_format": named_format()},
        ):
            self.assertEqual(run_hook(kwargs), kwargs)


if __name__ == "__main__":
    unittest.main()
