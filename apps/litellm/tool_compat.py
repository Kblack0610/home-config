"""LiteLLM proxy pre-call hook: tool-calling compatibility fixes.

1. Tool message content must be a string for OpenAI-compatible upstreams.
   home-llm sends tool results as JSON objects (it must: the voice_satellite
   card draws images and cards from the object in HA's chat log). Lazer's
   gpt-oss rejects that with 400 "tool.content: Input should be a valid
   string", so every second round fell back to the slower Gemini route
   (2026-10-15). The hook JSON-encodes non-string tool content; the card is
   unaffected because it reads HA's chat log, not this request.

2. Gemini rejects required-only anyOf branches:

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

3. Lazer's Claude models refuse a named response_format:

Lazer copies OpenAI's response_format.json_schema.name into Anthropic's
output_config.format, and Anthropic answers 400 "output_config.format.name:
Extra inputs are not permitted". Claude Code's structured side calls hit this
on every Lazer Claude route, because the /v1/messages bridge always sets the
name OpenAI requires (2026-11-06). Without the name Lazer sends a valid format
and the output stays schema-constrained. This runs as the deployment hook
because the bridge builds response_format after async_pre_call_hook, and the
new dict keeps the name for any fallback to a route that needs it. Revisit when
a named json_schema to `claude-sonnet-5` stops returning that 400: then the
strip can go.

4. OpenCode Go refuses a call without an x-opencode-session header:

Go answers 400 "Request is missing x-opencode-session" (MissingSessionID).
opencode sends a stable one per conversation and the gateway forwards it, but
any other client of an OpenAI-compatible endpoint has no reason to know about
the header: a scratch eval script on the binks-eval key sent 8 such 400s on
2026-11-13, after a doc and llm-probe (2026-11-12) had told callers to add it.
The hook gives a call that arrives without one a fresh per-request id, on the
routes listed in model_group_settings.forward_client_headers_to_llm_api (the
Go and opencode-free routes; that list is the one place that names them). A
caller's own non-empty header always wins. It goes into data["headers"], the dict
LiteLLM fills with the forwarded client headers before this hook runs.
"""

import json
import uuid
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger

LAZER_HOST = "proxy.lazertechnologies.com"
SESSION_HEADER = "x-opencode-session"


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


def header_forwarding_groups() -> list:
    settings = getattr(litellm, "model_group_settings", None)
    return getattr(settings, "forward_client_headers_to_llm_api", None) or []


def default_opencode_session(data: dict) -> None:
    if data.get("model") not in header_forwarding_groups():
        return
    headers = data.get("headers")
    if not isinstance(headers, dict):
        headers = data["headers"] = {}
    for key in [k for k in headers if k.lower() == SESSION_HEADER]:
        if headers[key]:
            return
        del headers[key]
    headers[SESSION_HEADER] = str(uuid.uuid4())


class ToolCompat(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):
        default_opencode_session(data)
        for msg in data.get("messages") or []:
            if isinstance(msg, dict) and msg.get("role") == "tool":
                content = msg.get("content")
                if content is not None and not isinstance(content, str):
                    msg["content"] = json.dumps(content, ensure_ascii=False)
        tools = data.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                fn = tool.get("function") if isinstance(tool, dict) else None
                if isinstance(fn, dict) and "parameters" in fn:
                    fn["parameters"] = strip_required_only_branches(fn["parameters"])
        return data

    async def async_pre_call_deployment_hook(self, kwargs: dict, call_type):
        response_format = kwargs.get("response_format")
        json_schema = response_format.get("json_schema") if isinstance(response_format, dict) else None
        # api_base, because the model reads openai/claude-x from /v1/chat/completions
        # and bare claude-x from the /v1/messages bridge.
        if (
            isinstance(json_schema, dict)
            and "claude-" in str(kwargs.get("model") or "")
            and LAZER_HOST in str(kwargs.get("api_base") or "")
        ):
            kept = {k: v for k, v in json_schema.items() if k != "name"}
            kwargs["response_format"] = {**response_format, "json_schema": kept}
        return kwargs


proxy_handler_instance = ToolCompat()
