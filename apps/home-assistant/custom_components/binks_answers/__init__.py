"""Binks Answers: a look_up LLM tool that answers like Google Home, better.

One HA LLM API ("binks_answers") with one tool, `look_up`. It searches the
in-cluster SearXNG, hands the model the top results' snippets to summarize,
and gives every result its own picture, so the voice_satellite card shows the
spoken summary beside photos of the very recipes/pages it is talking about.

Configured from YAML (config/packages/binks_answers.yaml), shipped from git by
the sync-managed-config init container. See docs/home-assistant-voice.md.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, llm
from homeassistant.helpers.typing import ConfigType

from .tool import DOMAIN, BinksAnswersAPI

CONF_SEARXNG_URL = "searxng_url"

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_SEARXNG_URL): cv.url})},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Binks Answers LLM API."""
    if DOMAIN not in config:
        return True
    url = config[DOMAIN][CONF_SEARXNG_URL].rstrip("/")
    llm.async_register_api(hass, BinksAnswersAPI(hass, url))
    return True
