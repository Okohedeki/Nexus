"""Research thread orchestration — discover and ingest new sources for an article."""

import json
import logging
import os
import re

from services.knowledge_graph import (
    add_discovery,
    get_generated_content,
    get_source_id_by_url,
    get_thread,
    list_discoveries,
    update_thread,
)
from services.providers.detection import get_provider
from services.web_search import search_web

logger = logging.getLogger(__name__)


_QUERY_PROMPT = """You generate web-search queries for an "evolving story" research thread.

ARTICLE TITLE: {title}

ARTICLE TOPIC / SUMMARY:
{summary}

KEY ENTITIES (from this article): {entities}

GRAPH NEIGHBORS (entities co-occurring with the article's entities elsewhere
in the user's knowledge graph — strong candidates for bridging stories):
{neighbors}

USER'S RESEARCH FOCUS (highest priority when present):
{focus}

Produce 3 distinct, focused search queries that will surface NEW articles, news,
or analyses related to this article. Apply this priority:
1. If the user's research focus is set, weight the queries strongly toward it.
2. At least ONE query should be a "bridge query" — combining the article's
   subject with a graph neighbor — to surface stories that connect this
   article to other threads already in the user's graph.
3. Otherwise, focus on recent developments, related angles, or counterpoints.

Avoid duplicating the article's own title verbatim.

Return ONLY a JSON array of 3 strings. No explanation. Example:
["query one", "query two", "query three"]
"""


async def _gather_thread_context(db, thread: dict) -> dict:
    """Pull article title, body excerpt, top entities, and graph-neighbor entities."""
    gen = await get_generated_content(db, thread["generated_id"])
    if not gen:
        raise ValueError(f"Generated content {thread['generated_id']} not found")

    title = gen.get("title") or "Untitled"
    body = (gen.get("content") or "")[:2000]

    cursor = await db.execute(
        """SELECT e.name
           FROM entities e
           JOIN entity_sources es ON es.entity_id = e.id
           JOIN generated_content_sources gcs ON gcs.source_id = es.source_id
           WHERE gcs.generated_id = ?
           GROUP BY e.id
           ORDER BY COUNT(*) DESC
           LIMIT 8""",
        (thread["generated_id"],),
    )
    entity_names = [row[0] for row in await cursor.fetchall()]

    # Graph neighbors: entities that share sources with the article's entities,
    # but are NOT themselves in the article. These are natural bridges to
    # connecting stories already in the user's knowledge graph.
    cursor = await db.execute(
        """SELECT e.name, COUNT(DISTINCT es2.source_id) AS shared
           FROM entities e
           JOIN entity_sources es2 ON es2.entity_id = e.id
           WHERE es2.source_id IN (
               SELECT DISTINCT es.source_id
               FROM entity_sources es
               WHERE es.entity_id IN (
                   SELECT DISTINCT es_a.entity_id
                   FROM entity_sources es_a
                   JOIN generated_content_sources gcs ON gcs.source_id = es_a.source_id
                   WHERE gcs.generated_id = ?
               )
           )
           AND e.id NOT IN (
               SELECT DISTINCT es_b.entity_id
               FROM entity_sources es_b
               JOIN generated_content_sources gcs2 ON gcs2.source_id = es_b.source_id
               WHERE gcs2.generated_id = ?
           )
           GROUP BY e.id
           ORDER BY shared DESC
           LIMIT 10""",
        (thread["generated_id"], thread["generated_id"]),
    )
    neighbors = [row[0] for row in await cursor.fetchall()]

    return {
        "title": title,
        "summary": body,
        "entities": entity_names,
        "neighbors": neighbors,
    }


async def generate_research_queries(db, thread: dict, model: str = "sonnet") -> list[str]:
    """Use LLM to derive 2-3 search queries from the article's content + entities + user focus."""
    ctx = await _gather_thread_context(db, thread)
    focus = (thread.get("focus_keywords") or "").strip()
    prompt = _QUERY_PROMPT.format(
        title=ctx["title"],
        summary=ctx["summary"] or "(no body)",
        entities=", ".join(ctx["entities"]) or "(none)",
        neighbors=", ".join(ctx.get("neighbors", [])) or "(none — graph is sparse)",
        focus=focus or "(none — derive purely from the article)",
    )

    provider = get_provider()
    try:
        raw, _cost = await provider.run_simple(prompt, model=model, timeout=60)
    except Exception as e:
        logger.warning("Query generation LLM call failed: %s — falling back to title", e)
        return [ctx["title"]]

    # Extract JSON array
    match = re.search(r"\[[\s\S]*\]", raw or "")
    if not match:
        logger.warning("Query LLM returned non-JSON; falling back to title. Raw: %r", (raw or "")[:200])
        return [ctx["title"]]
    try:
        queries = json.loads(match.group())
        queries = [str(q).strip() for q in queries if isinstance(q, str) and q.strip()]
        return queries[:3] or [ctx["title"]]
    except json.JSONDecodeError:
        return [ctx["title"]]


async def _already_discovered_urls(db, thread_id: int) -> set[str]:
    cursor = await db.execute(
        """SELECT s.url FROM sources s
           JOIN research_discoveries d ON d.source_id = s.id
           WHERE d.thread_id = ?""",
        (thread_id,),
    )
    return {row[0] for row in await cursor.fetchall() if row[0]}


async def run_research_poll(db, thread_id: int, model: str = "sonnet") -> dict:
    """Discover + ingest new sources for one thread. Returns summary dict."""
    thread = await get_thread(db, thread_id)
    if not thread:
        return {"success": False, "error": "Thread not found"}

    queries = await generate_research_queries(db, thread, model=model)
    logger.info("Thread %s queries: %s", thread_id, queries)

    seen_urls = await _already_discovered_urls(db, thread_id)
    seen_urls_lower = {u.lower() for u in seen_urls}

    candidates = []  # list of (url, title, snippet, query)
    per_query = max(3, thread["max_per_poll"] * 2)
    for q in queries:
        results = await search_web(q, max_results=per_query)
        for r in results:
            url = (r.get("url") or "").strip()
            if not url:
                continue
            if url.lower() in seen_urls_lower:
                continue
            seen_urls_lower.add(url.lower())
            candidates.append({"url": url, "title": r.get("title", ""),
                                "snippet": r.get("snippet", ""), "query": q})

    cap = thread["max_per_poll"]
    candidates = candidates[:cap]

    from services.ingestion_service import ingest_url
    tmp_dir = os.path.join(
        os.path.dirname(os.environ.get("KG_DB_PATH", "data/knowledge.db")), "tmp"
    )

    discovered = []
    skipped = []
    failed = []
    total_cost = 0.0

    for cand in candidates:
        url = cand["url"]
        existing_sid = await get_source_id_by_url(db, url)
        if existing_sid:
            disc_id = await add_discovery(db, thread_id, existing_sid, query=cand["query"])
            if disc_id:
                discovered.append({"source_id": existing_sid, "url": url, "reused": True})
            else:
                skipped.append(url)
            continue
        result = await ingest_url(db, url, model=model, tmp_dir=tmp_dir)
        if not result.get("success"):
            failed.append({"url": url, "error": result.get("error", "?")})
            continue
        sid = result["source_id"]
        total_cost += result.get("cost_usd", 0.0)
        await add_discovery(db, thread_id, sid, query=cand["query"])
        discovered.append({"source_id": sid, "url": url, "title": result.get("title", "")})

    await update_thread(db, thread_id, mark_polled=True)
    return {
        "success": True,
        "thread_id": thread_id,
        "queries": queries,
        "discovered": discovered,
        "skipped": skipped,
        "failed": failed,
        "discovered_count": len(discovered),
        "cost_usd": total_cost,
    }


async def get_thread_with_discoveries(db, generated_id: int) -> dict | None:
    """Convenience: thread row + discoveries list, or None."""
    from services.knowledge_graph import get_thread_for_generated
    thread = await get_thread_for_generated(db, generated_id)
    if not thread:
        return None
    thread["discoveries"] = await list_discoveries(db, thread["id"], limit=100)
    return thread


# ── Graph-level (article-free) topic research ───────────────────


_TOPIC_QUERY_PROMPT = """You generate web-search queries to expand the user's
knowledge graph around a hot topic.

TOPIC ENTITY: {entity_name}

NEIGHBORING ENTITIES IN THE GRAPH (entities that co-occur with the topic):
{neighbors}

Produce 3 distinct, focused search queries that will surface NEW articles or
analyses about this topic. At least ONE query should be a "bridge query" —
combining the topic with one of the neighboring entities — to grow the
spiderweb of connections.

Return ONLY a JSON array of 3 strings. No explanation. Example:
["query one", "query two", "query three"]
"""


async def _generate_topic_queries(db, entity_id: int, entity_name: str,
                                   model: str = "sonnet") -> list[str]:
    from services.graph_intel import gather_entity_neighbors
    neighbors = await gather_entity_neighbors(db, entity_id, limit=8)
    prompt = _TOPIC_QUERY_PROMPT.format(
        entity_name=entity_name,
        neighbors=", ".join(neighbors) or "(none — entity stands alone)",
    )
    provider = get_provider()
    try:
        raw, _cost = await provider.run_simple(prompt, model=model, timeout=60)
    except Exception as e:
        logger.warning("Topic query generation failed: %s — falling back to entity name", e)
        return [entity_name]
    match = re.search(r"\[[\s\S]*\]", raw or "")
    if not match:
        return [entity_name]
    try:
        queries = json.loads(match.group())
        queries = [str(q).strip() for q in queries if isinstance(q, str) and q.strip()]
        return queries[:3] or [entity_name]
    except json.JSONDecodeError:
        return [entity_name]


async def run_topic_poll(db, entity_id: int, entity_name: str,
                          max_per_poll: int = 3, model: str = "sonnet") -> dict:
    """Search and ingest new sources for a graph topic (no article required)."""
    from services.graph_intel import record_attention
    from services.knowledge_graph import get_source_id_by_url
    from services.ingestion_service import ingest_url
    import os

    queries = await _generate_topic_queries(db, entity_id, entity_name, model=model)
    logger.info("Topic %s queries: %s", entity_name, queries)

    seen = set()
    candidates = []
    for q in queries:
        results = await search_web(q, max_results=max(3, max_per_poll * 2))
        for r in results:
            url = (r.get("url") or "").strip()
            if not url or url.lower() in seen:
                continue
            seen.add(url.lower())
            candidates.append({"url": url, "query": q,
                                "title": r.get("title", ""), "snippet": r.get("snippet", "")})
    candidates = candidates[:max_per_poll]

    tmp_dir = os.path.join(
        os.path.dirname(os.environ.get("KG_DB_PATH", "data/knowledge.db")), "tmp"
    )
    discovered, skipped, failed = [], [], []
    cost = 0.0
    for cand in candidates:
        url = cand["url"]
        existing = await get_source_id_by_url(db, url)
        if existing:
            skipped.append(url)
            continue
        result = await ingest_url(db, url, model=model, tmp_dir=tmp_dir)
        if not result.get("success"):
            failed.append({"url": url, "error": result.get("error", "?")})
            continue
        cost += result.get("cost_usd", 0.0)
        discovered.append({"source_id": result["source_id"], "url": url,
                            "title": result.get("title", ""), "query": cand["query"]})

    await record_attention(db, entity_id, "research",
                            detail=f"discovered={len(discovered)}")
    return {
        "success": True,
        "topic": entity_name,
        "queries": queries,
        "discovered": discovered,
        "skipped": skipped,
        "failed": failed,
        "discovered_count": len(discovered),
        "cost_usd": cost,
    }


async def run_auto_topic_research_cycle(db, max_per_poll: int = 3,
                                          model: str = "sonnet") -> dict:
    """One sparse cycle: pick at most 1 hot topic and run a research poll on it."""
    from services.graph_intel import pick_topic_for_research
    pick = await pick_topic_for_research(db, cooldown_hours=72)
    if not pick:
        return {"success": True, "skipped": True, "reason": "no eligible topics"}
    logger.info("Auto-topic-research picked: %s (score=%.2f)",
                pick["name"], pick["score"])
    result = await run_topic_poll(db, pick["id"], pick["name"],
                                    max_per_poll=max_per_poll, model=model)
    result["score"] = pick["score"]
    return result
