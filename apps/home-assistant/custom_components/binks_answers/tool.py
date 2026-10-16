"""The look_up tool: SearXNG results, each with its own picture."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "binks_answers"
API_NAME = "Binks: Answers"

NUM_RESULTS = 5
# One shared budget for every page's og:image fetch. The search thumbnail is
# always there as the fallback, so a slow site costs a sharper picture, never
# the answer.
OG_BUDGET_S = 1.2
OG_MAX_BYTES = 300_000
SEARCH_TIMEOUT_S = 6
CACHE_TTL_S = 3600
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Video pages have no useful still and the card draws them as a broken tile.
SKIP_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "vimeo.com")

API_PROMPT = (
    "Use the look_up tool for recipes, how-to questions, places, products, news, "
    "and 'tell me about' or 'show me' requests about a topic. The wall screen "
    "shows a picture of each result next to your reply, so answer with a short "
    "spoken overview of what is on screen."
)
INSTRUCTION = (
    "Answer in two or three short spoken sentences built from these snippets. "
    "Name two or three of the results by their dish or page name, since their "
    "pictures are on screen. No URLs, no lists, no markdown."
)

_OG_RE = (
    re.compile(
        r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)(?::src)?[\"'][^>]*"
        r"content=[\"']([^\"']+)",
        re.I,
    ),
    re.compile(
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*"
        r"(?:property|name)=[\"'](?:og:image|twitter:image)(?::src)?[\"']",
        re.I,
    ),
)


def extract_og_image(html: str) -> str | None:
    """Return the page's og:image / twitter:image URL, if it is absolute."""
    for pattern in _OG_RE:
        if m := pattern.search(html):
            url = m.group(1).strip().replace("&amp;", "&")
            if url.startswith("http"):
                return url
    return None


def shape_results(raw: list[dict], og_images: dict[str, str]) -> list[dict]:
    """Turn SearXNG results into the card's results[] contract."""
    results = []
    for item in raw:
        url = item["url"]
        thumb = item.get("thumbnail") or ""
        image = og_images.get(url) or thumb
        results.append(
            {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("content", ""),
                # The card paints thumbnail_url first and opens image_url
                # large on tap; it filters results by image_url.
                "image_url": image,
                "thumbnail_url": thumb or image,
            }
        )
    return results


class LookUpTool(llm.Tool):
    """Search the web and return results that each carry a picture."""

    name = "look_up"
    description = (
        "Look something up on the web: recipes, how-to, places, products, news, "
        "facts. Returns the top results with snippets to summarize; each result's "
        "picture is shown on the wall screen."
    )
    parameters = vol.Schema(
        {vol.Required("query", description="What to search for"): str}
    )

    def __init__(self, searxng_url: str) -> None:
        super().__init__()
        self._searxng_url = searxng_url

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        query = tool_input.tool_args["query"]
        cache = hass.data.setdefault(DOMAIN, {})
        key = hashlib.md5(query.lower().strip().encode()).hexdigest()
        if (hit := cache.get(key)) and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
        try:
            raw = await self._search(hass, query)
            if not raw:
                return {"source": DOMAIN, "query": query, "results": [],
                        "message": "No results found."}
            og = await self._og_images(hass, [r["url"] for r in raw])
            response = {
                "source": DOMAIN,
                "query": query,
                "results": shape_results(raw, og),
                "instruction": INSTRUCTION,
            }
        except Exception as err:  # noqa: BLE001 - surface any failure to the model
            _LOGGER.exception("look_up failed for %r", query)
            return {"error": f"look_up failed: {err!s}"}
        cache[key] = (time.monotonic(), response)
        return response

    async def _search(self, hass: HomeAssistant, query: str) -> list[dict]:
        session = async_get_clientsession(hass)
        async with asyncio.timeout(SEARCH_TIMEOUT_S):
            async with session.get(
                f"{self._searxng_url}/search",
                params={"q": query, "format": "json", "categories": "general"},
                headers={"Accept": "application/json"},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        raw = []
        for item in data.get("results", []):
            url = item.get("url", "")
            host = (urlparse(url).hostname or "").removeprefix("www.")
            if not url.startswith("http") or host.endswith(SKIP_HOSTS):
                continue
            thumb = item.get("thumbnail") or item.get("img_src") or item.get("thumbnail_src") or ""
            raw.append({**item, "thumbnail": thumb if thumb.startswith("http") else ""})
            if len(raw) >= NUM_RESULTS:
                break
        return raw

    async def _og_images(self, hass: HomeAssistant, urls: list[str]) -> dict[str, str]:
        session = async_get_clientsession(hass)

        async def fetch(url: str) -> tuple[str, str | None]:
            try:
                async with session.get(
                    url, headers={"User-Agent": USER_AGENT}, allow_redirects=True
                ) as resp:
                    if resp.status != 200:
                        return url, None
                    body = await resp.content.read(OG_MAX_BYTES)
                    return url, extract_og_image(body.decode("utf-8", "ignore"))
            except Exception:  # noqa: BLE001 - best effort, thumbnail is the fallback
                return url, None

        tasks = [asyncio.create_task(fetch(u)) for u in urls]
        done, pending = await asyncio.wait(tasks, timeout=OG_BUDGET_S)
        for task in pending:
            task.cancel()
        return {url: img for url, img in (t.result() for t in done) if img}


class BinksAnswersAPI(llm.API):
    """LLM API exposing look_up."""

    def __init__(self, hass: HomeAssistant, searxng_url: str) -> None:
        super().__init__(hass=hass, id=DOMAIN, name=API_NAME)
        self._searxng_url = searxng_url

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[LookUpTool(self._searxng_url)],
        )
