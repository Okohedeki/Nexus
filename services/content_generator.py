"""Content generation from the knowledge graph — articles, newsletters, podcast scripts."""

import json
import logging

from services.knowledge_graph import (
    get_graph_context_for_query,
    get_source_entities,
    get_sources_by_date_range,
    get_sources_by_ids,
    save_generated_content,
    search_entities,
)
from services.providers.detection import get_provider

logger = logging.getLogger(__name__)

_TEMPLATES = {
    "article": """You are a skilled writer creating a well-researched article from a personal knowledge base.

TOPIC: {topic}

SOURCE MATERIAL:
{source_material}

ENTITY RELATIONSHIPS:
{relationships}

{extra}

Write a comprehensive, publication-quality article:
- Open with a compelling hook and thesis
- Organize into clear sections with headers
- Synthesize insights across multiple sources — don't just summarize each one
- Use specific data points, quotes, and examples from the sources
- Draw connections between different pieces of information
- End with implications, takeaways, or a forward-looking conclusion
- Cite sources naturally (e.g., "According to [Source Title], ...")
- Target 1000-2000 words
- Format in clean Markdown""",

    "newsletter": """You are creating an edition of a personal newsletter/briefing.

THEME: {topic}

AVAILABLE CONTENT:
{source_material}

{extra}

Create a newsletter edition:
- Start with a brief editorial introduction (2-3 sentences setting context)
- Organize into 3-5 themed sections
- Each section: brief intro, 2-3 key items with analysis (not just summaries)
- Include a "Quick Links" section for items that don't warrant full analysis
- End with a "What I'm Watching" section
- Conversational but informed tone
- Format in Markdown with clear headers""",

    "podcast_script": """You are writing a podcast script covering content from a personal knowledge base.

CONTENT TO COVER:
{source_material}

{extra}

Write a conversational podcast script:
- Format: Single host "thinking out loud" style
- Duration: ~10-15 minutes of speaking (roughly 1500-2000 words)
- Open with a brief teaser of what's covered
- Transition naturally between topics
- Add brief context/background for each topic
- Include rhetorical questions and "what this means" analysis
- End with key takeaways and a sign-off
- Use paragraph breaks for natural pause points
- Mark sections with [SECTION: Topic Name] headers
- Mark emphasis with *asterisks* for vocal stress""",
}


async def generate_content(
    db,
    content_type: str,
    title_hint: str = "",
    topic: str = "",
    source_ids: list[int] | None = None,
    category_id: int | None = None,
    date_range: tuple[str, str] | None = None,
    model: str = "sonnet",
    extra_instructions: str = "",
) -> dict:
    """Generate content from the knowledge graph.

    Returns dict with keys: id, title, content, content_type, source_ids, cost_usd, success, error
    """
    if content_type not in _TEMPLATES:
        return {"success": False, "error": f"Unknown content type: {content_type}"}

    try:
        # Gather source material
        sources = []
        if source_ids:
            sources = await get_sources_by_ids(db, source_ids)
        elif date_range:
            sources = await get_sources_by_date_range(db, date_range[0], date_range[1], category_id)
        elif topic:
            # Find sources via entity search
            entities = await search_entities(db, topic, limit=10)
            seen = set()
            for ent in entities:
                cursor = await db.execute(
                    """SELECT s.id, s.url, s.title, s.source_type, s.content_text, s.summary,
                              s.ingested_at, COALESCE(s.is_note, 0) AS is_note
                       FROM sources s
                       JOIN entity_sources es ON es.source_id = s.id
                       WHERE es.entity_id = ?
                       LIMIT 5""",
                    (ent["id"],),
                )
                for row in await cursor.fetchall():
                    src = dict(row)
                    if src["id"] not in seen:
                        seen.add(src["id"])
                        sources.append(src)
                if len(seen) >= 15:
                    break

        if not sources:
            return {"success": False, "error": "No source material found for the given parameters."}

        used_ids = [s["id"] for s in sources]

        # Build source material text
        material_parts = []
        for s in sources[:15]:  # Cap at 15 sources
            title = s["title"] or s.get("url") or "Untitled"
            text = s["content_text"] or s["summary"] or ""
            entities = await get_source_entities(db, s["id"])
            entity_str = ", ".join(e["name"] for e in entities[:6])
            material_parts.append(
                f"### {title} ({s['source_type']})\n"
                f"{text[:2000]}\n"
                f"Entities: {entity_str}"
            )

        # Get relationship context if we have a topic
        relationships = ""
        if topic:
            relationships = await get_graph_context_for_query(db, topic)

        template = _TEMPLATES[content_type]
        prompt = template.format(
            topic=title_hint or topic or "Recent content",
            source_material="\n\n".join(material_parts),
            relationships=relationships or "(No specific relationship context)",
            extra=f"Additional instructions: {extra_instructions}" if extra_instructions else "",
        )

        provider = get_provider()
        content, cost_usd = await provider.run_simple(
            prompt, model=model, max_budget_usd=2.0, timeout=300,
        )

        if not content or not content.strip():
            return {"success": False, "error": "Provider returned empty output"}

        # Generate title if not provided
        title = title_hint
        if not title:
            # Extract first heading or first line
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("#"):
                    title = line.lstrip("#").strip()
                    break
                if line and len(line) > 10:
                    title = line[:80]
                    break
            if not title:
                title = f"{content_type.replace('_', ' ').title()}: {topic or 'Untitled'}"

        gen_id = await save_generated_content(
            db, content_type, title, content,
            json.dumps({
                "topic": topic,
                "source_ids": used_ids,
                "category_id": category_id,
                "extra_instructions": extra_instructions,
            }),
            model, cost_usd, used_ids,
        )

        return {
            "id": gen_id,
            "title": title,
            "content": content,
            "content_type": content_type,
            "source_ids": used_ids,
            "cost_usd": cost_usd,
            "success": True,
        }

    except Exception as e:
        logger.exception("Content generation failed")
        return {"success": False, "error": str(e)}
