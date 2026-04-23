"""Content generation from the knowledge graph — articles, newsletters, podcast scripts."""

import json
import logging
import os
import time
from pathlib import Path

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

    "podcast_script": """You are writing a professional podcast briefing covering content from a personal knowledge base.

CONTENT TO COVER:
{source_material}

{extra}

Write a polished, professional podcast script suitable for a news/analysis show.
Tone: confident, journalistic, measured — think NPR's "Marketplace" or "The Daily",
not casual chat or self-conscious narration.

REQUIREMENTS
- Single host. Use first-person sparingly ("Today on the briefing…").
- Open with a tight cold open (2-3 sentences) framing the central thread before
  the formal introduction.
- Duration target: ~10-15 minutes spoken (roughly 1500-2000 words).
- Transition cleanly between topics with short framing sentences.
- Add concise context for each topic before analysis. Use specific names, dates,
  numbers, and direct quotes from the source material whenever present.
- Keep sentences short to medium-length for clear vocal delivery.
- Close with a brief synthesis ("Putting it together…") and a quiet sign-off.

STRICT FORMAT RULES (these affect the audio)
- Output ONLY the spoken script. No host names, no music cues, no stage
  directions, no [SECTION:] tags, no headers, no markdown bullets.
- Use plain paragraph breaks for natural pauses.
- Use *asterisks* very sparingly to mark a single emphasized word when it
  truly matters. Default to no emphasis.""",
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

        # Synthesize audio for podcasts
        audio_path = None
        if content_type == "podcast_script":
            try:
                from services.tts import synthesize_to_file
                db_path = os.environ.get("KG_DB_PATH") or os.path.join(os.getcwd(), "data", "knowledge.db")
                audio_dir = Path(db_path).parent / "audio"
                safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)[:60].strip() or "podcast"
                out = audio_dir / f"{int(time.time())}_{safe_title}.wav"
                voice = os.environ.get("PODCAST_VOICE", "bm_george")
                speed = float(os.environ.get("PODCAST_SPEED", "0.95"))
                audio_path = str(await synthesize_to_file(content, out, voice=voice, speed=speed))
            except Exception as e:
                logger.exception("TTS synthesis failed — returning script without audio")
                audio_path = None

        parameters = {
            "topic": topic,
            "source_ids": used_ids,
            "category_id": category_id,
            "extra_instructions": extra_instructions,
        }
        if audio_path:
            parameters["audio_path"] = audio_path

        gen_id = await save_generated_content(
            db, content_type, title, content,
            json.dumps(parameters),
            model, cost_usd, used_ids,
        )

        # Auto-create a research thread for every new article (best-effort)
        if content_type == "article":
            try:
                from services.knowledge_graph import create_thread, get_thread_for_generated
                if not await get_thread_for_generated(db, gen_id):
                    await create_thread(db, gen_id, cadence_hours=24, max_per_poll=5)
                    logger.info("Auto-created research thread for article %s", gen_id)
            except Exception:
                logger.exception("Failed to auto-create research thread for article %s", gen_id)

        return {
            "id": gen_id,
            "title": title,
            "content": content,
            "content_type": content_type,
            "source_ids": used_ids,
            "cost_usd": cost_usd,
            "audio_path": audio_path,
            "success": True,
        }

    except Exception as e:
        logger.exception("Content generation failed")
        return {"success": False, "error": str(e)}
