"""Web search tool for the autonomous agent.

Uses the DuckDuckGo HTML search (via ``duckduckgo_search``) to fetch results
without requiring any API keys.  Falls back to a clear error message when
the library is missing or the request fails.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


def web_search(query: str) -> list[dict[str, str]]:
    """Search the web for *query* and return up to ``MAX_RESULTS`` hits.

    Each result is a dict with keys ``title``, ``snippet``, and ``url``.

    Parameters
    ----------
    query:
        Free-text search query (e.g. "python asyncio tutorial").

    Returns
    -------
    list[dict[str, str]]
        A list of search result dicts.  On error the list contains a
        single dict with an ``"error"`` key describing the problem.
    """
    if not query or not query.strip():
        return [{"error": "Search query must not be empty."}]

    try:
        return _search_via_ddgs(query)
    except ImportError:
        logger.warning("duckduckgo_search is not installed — falling back to httpx scraper")
        return _search_via_httpx(query)
    except Exception as exc:
        logger.error("web_search failed: %s", exc, exc_info=True)
        return [{"error": f"Search failed: {exc}"}]


# ── Strategy 1: duckduckgo_search library ───────────────────────────

def _search_via_ddgs(query: str) -> list[dict[str, str]]:
    """Use the ``duckduckgo_search`` package (DDGS.text)."""
    from duckduckgo_search import DDGS  # type: ignore[import-untyped]

    results: list[dict[str, str]] = []

    with DDGS() as ddgs:
        for hit in ddgs.text(query, max_results=MAX_RESULTS):
            results.append({
                "title": hit.get("title", ""),
                "snippet": hit.get("body", ""),
                "url": hit.get("href", ""),
            })

    if not results:
        return [{"error": "No results found."}]

    return results


# ── Strategy 2: lightweight httpx fallback (DuckDuckGo Lite) ────────

def _search_via_httpx(query: str) -> list[dict[str, str]]:
    """Minimal fallback using ``httpx`` to query DuckDuckGo Lite HTML.

    This is intentionally simple — it parses the DuckDuckGo Lite page
    which has a predictable structure.  It is only used when the
    ``duckduckgo_search`` package is not installed.
    """
    import re

    import httpx

    try:
        resp = httpx.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; TGAgent/1.0)"},
            timeout=15.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("httpx fallback request failed: %s", exc)
        return [{"error": f"HTTP request failed: {exc}"}]

    html = resp.text
    results: list[dict[str, str]] = []

    # DuckDuckGo Lite wraps each result link in <a class="result-link">
    link_pattern = re.compile(
        r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.+?)</a>',
        re.DOTALL,
    )
    # Snippet lives in <td class="result-snippet">...</td>
    snippet_pattern = re.compile(
        r'<td\s+class="result-snippet">(.*?)</td>',
        re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for idx, (url, raw_title) in enumerate(links[:MAX_RESULTS]):
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        snippet = ""
        if idx < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[idx]).strip()
        results.append({"title": title, "snippet": snippet, "url": url})

    if not results:
        return [{"error": "No results could be extracted from DuckDuckGo Lite."}]

    return results
