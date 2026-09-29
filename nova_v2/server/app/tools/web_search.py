"""
web_search.py - the web_search context tool, answered by a self-hosted SearXNG.

It used to be Anthropic's server-side search, which ran inside the model call.
The local model has nothing like it, so NOVA runs the search itself: SearXNG's
JSON API (deploy/searxng), top few results as title / url / snippet, and the
model writes the answer from those.

A context tool like get_current_address: no gain, no Action recorded. Capped
per turn in intent_surface.py (WEB_SEARCH_MAX_USES), as max_uses capped the
hosted one.
"""
from __future__ import annotations

import os
from typing import Any

import requests

SEARXNG_URL = os.environ.get("SEARXNG_URL", "").rstrip("/")
RESULTS = 5
TIMEOUT_S = 4.0

WEB_SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Searches the web and returns the top results as title, url and "
        "snippet. Use it for anything current or local you don't already "
        "know: opening hours, news, weather, prices, places near the user. "
        "Write a short spoken answer from the snippets; never read out URLs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
        },
        "required": ["query"],
    },
}


def web_search(query: str, base_url: str | None = None) -> dict[str, Any]:
    base = (base_url or SEARXNG_URL).rstrip("/")
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "empty query"}
    if not base:
        return {"success": False, "error": "web search is not configured"}
    try:
        r = requests.get(
            f"{base}/search",
            params={"q": query, "format": "json", "safesearch": 1},
            timeout=TIMEOUT_S,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[web_search] failed: {e}")
        return {"success": False, "error": "search unavailable right now"}

    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:400],
        }
        for item in data.get("results", [])[:RESULTS]
    ]
    answers = [a for a in data.get("answers", []) if isinstance(a, str)][:2]
    out: dict[str, Any] = {"success": True, "query": query, "results": results}
    if answers:
        out["answers"] = answers
    if not results and not answers:
        out["note"] = "no results"
    return out
