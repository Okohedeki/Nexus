"""Platform-agnostic message handlers.

Telegram/Discord are ingestion pipes — send URLs or media and they get
extracted, analyzed, and stored in the knowledge graph.
"""

import logging
import os

from core.auth import authorized
from services.content_extractor import extract_from_file
from services.entity_extractor import extract_entities
from services.ingestion_service import ingest_url
from services.knowledge_graph import (
    add_entity,
    add_relationship,
    add_source,
    get_entity_by_name,
    link_entity_to_source,
)
from services.output_formatter import chunk_message

logger = logging.getLogger(__name__)


@authorized
async def handle_url_message(ctx):
    """Handle messages containing URLs — extract, analyze, and store."""
    urls = ctx.extract_urls()
    if not urls:
        return

    config = ctx.get_config()
    db = ctx.get_db()
    tmp_dir = os.path.join(os.path.dirname(config.kg_db_path), "tmp")

    status_msg = await ctx.message.reply(f"Ingesting {len(urls)} URL(s)...")

    for i, url in enumerate(urls):
        label = f"[{i + 1}/{len(urls)}]" if len(urls) > 1 else ""
        try:
            if label:
                await status_msg.edit(f"{label} Processing: {url}")

            result = await ingest_url(
                db, url,
                model=config.default_model,
                whisper_model=config.whisper_model,
                tmp_dir=tmp_dir,
                chat_id=0,
            )

            if not result["success"]:
                await ctx.message.reply(f"{label} Failed: {result.get('error', 'Unknown error')}")
                continue

            lines = [f"Ingested: {result['title'] or url}"]
            lines.append(f"{result['entity_count']} entities, {result['rel_count']} relationships")
            if result.get("summary"):
                lines.append(f"\n{result['summary']}")

            try:
                await status_msg.edit("\n".join(lines))
            except Exception:
                await ctx.message.reply("\n".join(lines))

        except Exception as e:
            logger.exception("Failed to process URL: %s", url)
            await ctx.message.reply(f"{label} Error: {e}")


@authorized
async def handle_media_message(ctx):
    """Handle voice, audio, video messages — transcribe and store."""
    config = ctx.get_config()
    db = ctx.get_db()
    tmp_dir = os.path.join(os.path.dirname(config.kg_db_path), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    attachment = await ctx.download_attachment(tmp_dir)
    if not attachment:
        return

    file_path, source_type = attachment
    status_msg = await ctx.message.reply(f"Transcribing {source_type}...")

    try:
        content = await extract_from_file(file_path, source_type, config.whisper_model)

        if not content.success:
            await status_msg.edit(f"Transcription failed: {content.error}")
            return

        if not content.content_text.strip():
            await status_msg.edit("Transcription returned empty text.")
            return

        await status_msg.edit("Analyzing content...")

        extraction = await extract_entities(
            content.content_text,
            content.title,
            content.source_type,
            model=config.default_model,
        )

        summary = extraction.get("summary", "")
        source_id = await add_source(
            db, content.url, content.title, content.source_type,
            content.content_text, summary, 0,
        )

        entity_count = 0
        rel_count = 0

        if extraction["success"]:
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

        lines = [f"Ingested: {source_type} message"]
        lines.append(f"{entity_count} entities, {rel_count} relationships")
        if summary:
            lines.append(f"\n{summary}")

        chunks = chunk_message("\n".join(lines), ctx.max_message_length)
        try:
            await status_msg.edit(chunks[0])
        except Exception:
            await ctx.message.reply(chunks[0])
        for chunk in chunks[1:]:
            await ctx.message.reply(chunk)

    except Exception as e:
        logger.exception("Media processing failed")
        await status_msg.edit(f"Error: {e}")
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
