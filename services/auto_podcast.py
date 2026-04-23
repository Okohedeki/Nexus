"""Graph-driven auto-podcast — periodically pick an interesting topic from the
knowledge graph and generate a podcast briefing on it (with audio).
"""

import logging

from services.content_generator import generate_content
from services.graph_intel import (
    gather_entity_recent_sources,
    pick_topic_for_podcast,
    record_attention,
)

logger = logging.getLogger(__name__)


async def run_topic_podcast(db, entity_id: int, entity_name: str, model: str = "sonnet") -> dict:
    """Generate a podcast briefing focused on the given entity."""
    sources = await gather_entity_recent_sources(db, entity_id, limit=10)
    if not sources:
        return {"success": False, "error": f"No sources for entity {entity_name}"}

    source_ids = [s["id"] for s in sources]
    extra = (
        f"This is a focused briefing on {entity_name}. Synthesize across the "
        f"provided sources, surfacing patterns, contradictions, and emerging "
        f"angles. Treat it as an evolving thread the listener has been following."
    )

    result = await generate_content(
        db,
        content_type="podcast_script",
        topic=entity_name,
        title_hint=f"Briefing: {entity_name}",
        source_ids=source_ids,
        model=model,
        extra_instructions=extra,
    )
    if result.get("success"):
        await record_attention(
            db, entity_id, "podcast",
            detail=f"generated_id={result.get('id')}",
        )
    return result


async def run_auto_podcast_cycle(db, model: str = "sonnet") -> dict:
    """One sparse cycle: pick at most 1 interesting topic and brief it."""
    pick = await pick_topic_for_podcast(db, cooldown_hours=168, min_recent=3)
    if not pick:
        return {"success": True, "skipped": True, "reason": "no fresh interesting topics"}
    logger.info("Auto-podcast picked: %s (score=%.2f, recent=%d)",
                pick["name"], pick["score"], pick["recent_count"])
    result = await run_topic_podcast(db, pick["id"], pick["name"], model=model)
    result["topic"] = pick["name"]
    result["score"] = pick["score"]
    return result
