"""Natural language search over the knowledge graph."""

import logging

from services.knowledge_graph import (
    get_graph_context_for_query,
    get_source_entities,
    search_entities,
)
from services.providers.detection import get_provider

logger = logging.getLogger(__name__)

_SEARCH_PROMPT = """You are a research assistant answering questions from a personal knowledge graph.

CONTEXT FROM KNOWLEDGE GRAPH:
{graph_context}

RELEVANT SOURCE CONTENTS:
{source_contents}

QUESTION: {query}

Instructions:
- Answer ONLY from the provided context. If the context doesn't contain enough information, say so clearly.
- Be thorough but concise. Use specific details from the sources.
- Cite sources using the format [Source Title] after relevant claims.
- Structure your answer with Markdown if it helps clarity.
- If the question is vague, interpret it broadly and cover the main themes present in the context."""


async def search_knowledge_graph(db, query: str, model: str = "sonnet") -> dict:
    """Search the knowledge graph with a natural language question.

    Returns dict with keys: answer, source_ids, cost_usd, success, error
    """
    try:
        graph_context = await get_graph_context_for_query(db, query)

        # Get full content for sources matching the query entities
        source_contents = []
        source_ids = []
        entities = await search_entities(db, query, limit=10)
        seen_sources = set()

        for entity in entities[:5]:
            src_entities = await get_source_entities(db, entity["id"])
            # get_source_entities returns entities, not sources — we need the source_id
            # Use entity_sources junction to find sources
            cursor = await db.execute(
                """SELECT s.id, s.title, s.source_type, s.content_text, s.summary
                   FROM sources s
                   JOIN entity_sources es ON es.source_id = s.id
                   WHERE es.entity_id = ?
                   LIMIT 3""",
                (entity["id"],),
            )
            for row in await cursor.fetchall():
                src = dict(row)
                if src["id"] not in seen_sources:
                    seen_sources.add(src["id"])
                    source_ids.append(src["id"])
                    text = src["content_text"] or src["summary"] or ""
                    source_contents.append(
                        f"### {src['title'] or 'Untitled'} ({src['source_type']})\n"
                        f"{text[:2000]}"
                    )
                if len(seen_sources) >= 5:
                    break
            if len(seen_sources) >= 5:
                break

        prompt = _SEARCH_PROMPT.format(
            graph_context=graph_context,
            source_contents="\n\n".join(source_contents) if source_contents else "(No detailed content available)",
            query=query,
        )

        provider = get_provider()
        answer, cost_usd = await provider.run_simple(
            prompt, model=model, max_budget_usd=0.50, timeout=120,
        )

        return {
            "answer": answer,
            "source_ids": source_ids,
            "cost_usd": cost_usd,
            "success": True,
            "error": None,
        }

    except Exception as e:
        logger.exception("NL search failed")
        return {
            "answer": "",
            "source_ids": [],
            "cost_usd": 0.0,
            "success": False,
            "error": str(e),
        }
