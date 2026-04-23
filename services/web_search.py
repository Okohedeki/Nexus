"""Thin async wrapper around the `ddgs` package (DuckDuckGo)."""

import asyncio
import logging

logger = logging.getLogger(__name__)


def _ddg_text(query: str, max_results: int) -> list[dict]:
    """Synchronous DDG call — runs inside asyncio.to_thread."""
    from ddgs import DDGS
    out = []
    try:
        for r in DDGS().text(query, max_results=max_results):
            url = r.get("href") or r.get("url") or ""
            if not url:
                continue
            out.append({
                "url": url,
                "title": r.get("title") or "",
                "snippet": r.get("body") or r.get("snippet") or "",
            })
    except Exception as e:
        logger.warning("DDGS query %r failed: %s", query, e)
    return out


async def search_web(query: str, max_results: int = 10) -> list[dict]:
    """Return a list of {url, title, snippet} dicts. Empty list on failure."""
    if not query or not query.strip():
        return []
    try:
        results = await asyncio.to_thread(_ddg_text, query, max_results)
        return results
    except Exception as e:
        logger.warning("search_web wrapper failure for %r: %s", query, e)
        return []
