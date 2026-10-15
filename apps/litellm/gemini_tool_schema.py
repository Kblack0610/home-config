"""LiteLLM proxy pre-call hook: make OpenAI tool schemas acceptable to Gemini.

Home Assistant's timer intents (HassStartTimer, HassIncreaseTimer, ...)
declare "at least one of hours/minutes/seconds" as

    {"type": "object", "properties": {...},
     "anyOf": [{"required": ["hours"]}, {"required": ["minutes"]}, ...]}

OpenAI accepts that. Gemini rejects the whole request with
"anyOf[0].required[0]: property is not defined", because each anyOf branch
must define the properties it requires. HA only offers timer tools to
satellites, so every voice turn from a wall tablet failed (2026-10-15) while
the same question with no device worked.

The fix drops anyOf/oneOf branches that carry nothing but `required`: the
properties stay, only the at-least-one constraint is lost, and HA still
validates the arguments when it runs the intent. Anything else is untouched.
"""

from typing import Any

from litellm.integrations.custom_logger import CustomLogger


def strip_required_only_branches(schema: Any) -> Any:
    if isinstance(schema, list):
        return [strip_required_only_branches(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in ("anyOf", "oneOf") and isinstance(value, list):
            kept = [
                b for b in value
                if not (isinstance(b, dict) and b and set(b) <= {"required"})
            ]
            if kept:
                out[key] = strip_required_only_branches(kept)
            continue
        out[key] = strip_required_only_branches(value)
    return out


class GeminiToolSchema(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):
        tools = data.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                fn = tool.get("function") if isinstance(tool, dict) else None
                if isinstance(fn, dict) and "parameters" in fn:
                    fn["parameters"] = strip_required_only_branches(fn["parameters"])
        return data


proxy_handler_instance = GeminiToolSchema()
