"""Shared ingestion logic used by both Telegram bot and web API."""

import logging

from services.content_extractor import extract_content
from services.entity_extractor import extract_entities
from services.knowledge_graph import (
    add_entity,
    add_relationship,
    add_source,
    clear_source_entities,
    delete_source_by_url,
    get_entity_by_name,
    link_entity_to_source,
    update_note,
)

logger = logging.getLogger(__name__)


async def _link_extraction(db, source_id: int, extraction: dict) -> tuple[int, int]:
    """Link extracted entities and relationships to a source. Returns (entity_count, rel_count)."""
    entity_count = 0
    rel_count = 0

    if not extraction.get("success"):
        return entity_count, rel_count

    for ent in extraction["entities"]:
        ent_id = await add_entity(db, ent["name"], ent["type"], ent.get("description", ""))
        await link_entity_to_source(db, ent_id, source_id)
        entity_count += 1

    for rel in extraction["relationships"]:
        src_ent = await get_entity_by_name(db, rel["source"])
        tgt_ent = await get_entity_by_name(db, rel["target"])
        if src_ent and tgt_ent:
            await add_relationship(db, src_ent["id"], tgt_ent["id"], rel["type"])
            rel_count += 1

    return entity_count, rel_count


async def ingest_url(
    db,
    url: str,
    model: str = "sonnet",
    whisper_model: str = "base",
    tmp_dir: str = "data/tmp",
    chat_id: int = 0,
) -> dict:
    """Full URL ingestion pipeline: extract content → extract entities → store.

    Returns dict with keys: source_id, title, source_type, entity_count, rel_count,
    cost_usd, summary, success, error
    """
    try:
        # Remove old data if URL was previously ingested
        deleted = await delete_source_by_url(db, url)
        if deleted:
            logger.info("Replaced %d old source(s) for URL: %s", deleted, url)

        content = await extract_content(url, tmp_dir, whisper_model)

        if not content.success:
            return {"success": False, "error": content.error or "Could not extract content"}

        if not content.content_text.strip():
            return {"success": False, "error": "No text content found"}

        extraction = await extract_entities(
            content.content_text, content.title, content.source_type, model=model,
        )

        summary = extraction.get("summary", "")
        source_id = await add_source(
            db, url, content.title, content.source_type,
            content.content_text, summary, chat_id,
        )

        entity_count, rel_count = await _link_extraction(db, source_id, extraction)

        # Auto-categorize (best-effort; never fail the ingest if this errors)
        try:
            from services.categorizer import auto_categorize_source
            await auto_categorize_source(
                db, source_id, content.title or "",
                summary or content.content_text or "", model=model,
            )
        except Exception:
            logger.exception("Auto-categorize failed for source %s", source_id)

        return {
            "success": True,
            "source_id": source_id,
            "title": content.title,
            "source_type": content.source_type,
            "entity_count": entity_count,
            "rel_count": rel_count,
            "cost_usd": extraction.get("cost_usd", 0.0),
            "summary": summary,
        }

    except Exception as e:
        logger.exception("URL ingestion failed for %s", url)
        return {"success": False, "error": str(e)}


async def ingest_note_content(
    db,
    source_id: int,
    title: str,
    content_text: str,
    model: str = "sonnet",
) -> dict:
    """Extract entities from note text and link them to the source.

    Clears existing entity links first (for re-extraction on edit).
    Returns dict with keys: entity_count, rel_count, cost_usd, summary, success
    """
    try:
        # Clear old entity links
        await clear_source_entities(db, source_id)

        if not content_text.strip():
            return {"success": True, "entity_count": 0, "rel_count": 0, "cost_usd": 0.0, "summary": ""}

        extraction = await extract_entities(
            content_text, title, "note", model=model,
        )

        summary = extraction.get("summary", "")
        await update_note(db, source_id, title, content_text, summary)

        entity_count, rel_count = await _link_extraction(db, source_id, extraction)

        try:
            from services.categorizer import auto_categorize_source
            await auto_categorize_source(
                db, source_id, title or "",
                summary or content_text, model=model,
            )
        except Exception:
            logger.exception("Auto-categorize failed for note %s", source_id)

        return {
            "success": True,
            "entity_count": entity_count,
            "rel_count": rel_count,
            "cost_usd": extraction.get("cost_usd", 0.0),
            "summary": summary,
        }

    except Exception as e:
        logger.exception("Note entity extraction failed for source %d", source_id)
        return {"success": False, "error": str(e), "entity_count": 0, "rel_count": 0, "cost_usd": 0.0}
