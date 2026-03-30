"""Digest generation from recently ingested content."""

import json
import logging
from datetime import datetime, timedelta

from services.knowledge_graph import (
    get_source_entities,
    get_sources_by_date_range,
    get_stats,
    save_generated_content,
)
from services.providers.detection import get_provider

logger = logging.getLogger(__name__)

_DIGEST_PROMPT = """You are creating a {period} digest of content saved to a personal knowledge graph.

PERIOD: {start_date} to {end_date}
TOTAL ITEMS: {count}

CONTENT:

{source_sections}

Instructions:
- Write a polished digest newsletter in Markdown
- Start with a brief overview paragraph summarizing the main themes
- Group insights by topic/theme — synthesize, don't just list summaries
- Highlight connections between different items where relevant
- End with a "Key Takeaways" section with 3-5 bullet points
- Use **bold** for important terms
- Keep the tone professional but engaging
- Reference source titles for attribution"""


async def generate_digest(
    db,
    period: str = "weekly",
    model: str = "sonnet",
    category_id: int | None = None,
) -> dict:
    """Generate a digest from recently ingested content.

    Returns dict with keys: id, title, content, source_ids, source_count, cost_usd, success
    """
    try:
        now = datetime.now()
        if period == "daily":
            start = now - timedelta(days=1)
        else:
            start = now - timedelta(days=7)

        start_str = start.strftime("%Y-%m-%d %H:%M:%S")
        end_str = now.strftime("%Y-%m-%d %H:%M:%S")

        sources = await get_sources_by_date_range(db, start_str, end_str, category_id)

        if not sources:
            return {
                "success": False,
                "error": f"No content ingested in the last {'day' if period == 'daily' else 'week'}.",
            }

        # Build source sections with entity context
        sections = []
        source_ids = []
        for s in sources:
            source_ids.append(s["id"])
            entities = await get_source_entities(db, s["id"])
            entity_names = ", ".join(e["name"] for e in entities[:8])
            title = s["title"] or s.get("url") or "Untitled"
            summary = s["summary"] or (s["content_text"] or "")[:500]
            section = f"- **{title}** ({s['source_type']}): {summary}"
            if entity_names:
                section += f"\n  Entities: {entity_names}"
            sections.append(section)

        prompt = _DIGEST_PROMPT.format(
            period=period,
            start_date=start.strftime("%b %d"),
            end_date=now.strftime("%b %d, %Y"),
            count=len(sources),
            source_sections="\n".join(sections),
        )

        provider = get_provider()
        content, cost_usd = await provider.run_simple(
            prompt, model=model, max_budget_usd=1.0, timeout=180,
        )

        if not content or not content.strip():
            return {"success": False, "error": "Provider returned empty output"}

        title = f"{'Daily' if period == 'daily' else 'Weekly'} Digest: {start.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"
        content_type = f"digest_{period}"

        gen_id = await save_generated_content(
            db, content_type, title, content,
            json.dumps({"period": period, "start": start_str, "end": end_str}),
            model, cost_usd, source_ids,
        )

        return {
            "id": gen_id,
            "title": title,
            "content": content,
            "source_ids": source_ids,
            "source_count": len(sources),
            "cost_usd": cost_usd,
            "success": True,
        }

    except Exception as e:
        logger.exception("Digest generation failed")
        return {"success": False, "error": str(e)}
