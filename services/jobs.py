"""Background generation jobs — fire-and-forget content/digest generation."""

import asyncio
import json
import logging

import aiosqlite

from services.knowledge_graph import create_job, update_job

logger = logging.getLogger(__name__)


async def _with_db(db_path: str, coro_factory):
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        return await coro_factory(conn)
    finally:
        await conn.close()


async def _run_content_job(db_path: str, job_id: int, kwargs: dict):
    async def _work(db):
        from services.content_generator import generate_content
        await update_job(db, job_id, status="running", mark_started=True,
                         progress_note="Generating content…")
        try:
            result = await generate_content(db, **kwargs)
        except Exception as e:
            logger.exception("Job %s crashed", job_id)
            await update_job(db, job_id, status="failed",
                             error=str(e), mark_completed=True)
            return
        if result.get("success"):
            await update_job(db, job_id, status="completed",
                             result_id=result.get("id"),
                             cost_usd=result.get("cost_usd", 0.0),
                             title=result.get("title"),
                             progress_note=None,
                             mark_completed=True)
        else:
            await update_job(db, job_id, status="failed",
                             error=result.get("error", "Unknown error"),
                             mark_completed=True)
    await _with_db(db_path, _work)


async def _run_digest_job(db_path: str, job_id: int, kwargs: dict):
    async def _work(db):
        from services.digest_service import generate_digest
        await update_job(db, job_id, status="running", mark_started=True,
                         progress_note="Generating digest…")
        try:
            result = await generate_digest(db, **kwargs)
        except Exception as e:
            logger.exception("Digest job %s crashed", job_id)
            await update_job(db, job_id, status="failed",
                             error=str(e), mark_completed=True)
            return
        if result.get("success"):
            await update_job(db, job_id, status="completed",
                             result_id=result.get("id"),
                             cost_usd=result.get("cost_usd", 0.0),
                             title=result.get("title"),
                             progress_note=None,
                             mark_completed=True)
        else:
            await update_job(db, job_id, status="failed",
                             error=result.get("error", "Unknown error"),
                             mark_completed=True)
    await _with_db(db_path, _work)


async def queue_content_job(db, db_path: str, request: dict) -> int:
    """Create a content-generation job row and spawn background runner. Returns job_id."""
    title = request.get("title_hint") or request.get("topic") or "Untitled"
    job_id = await create_job(
        db,
        job_kind="content",
        content_type=request.get("content_type", "article"),
        title=title,
        params_json=json.dumps(request),
    )
    asyncio.create_task(_run_content_job(db_path, job_id, request))
    return job_id


async def queue_digest_job(db, db_path: str, request: dict) -> int:
    """Create a digest-generation job row and spawn background runner. Returns job_id."""
    period = request.get("period", "daily")
    job_id = await create_job(
        db,
        job_kind="digest",
        content_type=period,
        title=f"{period.title()} digest",
        params_json=json.dumps(request),
    )
    asyncio.create_task(_run_digest_job(db_path, job_id, request))
    return job_id


async def _run_research_job(db_path: str, job_id: int, thread_id: int, model: str):
    async def _work(db):
        from services.research import run_research_poll
        await update_job(db, job_id, status="running", mark_started=True,
                         progress_note="Discovering related sources…")
        try:
            result = await run_research_poll(db, thread_id, model=model)
        except Exception as e:
            logger.exception("Research job %s crashed", job_id)
            await update_job(db, job_id, status="failed",
                             error=str(e), mark_completed=True)
            return
        if result.get("success"):
            n = result.get("discovered_count", 0)
            note = f"{n} new source(s)" if n else "No new sources"
            await update_job(db, job_id, status="completed",
                             cost_usd=result.get("cost_usd", 0.0),
                             progress_note=note,
                             mark_completed=True)
        else:
            await update_job(db, job_id, status="failed",
                             error=result.get("error", "Unknown error"),
                             mark_completed=True)
    await _with_db(db_path, _work)


async def queue_research_job(db, db_path: str, thread_id: int,
                              article_title: str = "", model: str = "sonnet") -> int:
    """Create a research-poll job row and spawn background runner. Returns job_id."""
    job_id = await create_job(
        db,
        job_kind="research",
        content_type="poll",
        title=f"Research: {article_title or '#'+str(thread_id)}",
        params_json=json.dumps({"thread_id": thread_id, "model": model}),
    )
    asyncio.create_task(_run_research_job(db_path, job_id, thread_id, model))
    return job_id


async def _run_topic_research_job(db_path: str, job_id: int,
                                    entity_id: int, entity_name: str, model: str):
    async def _work(db):
        from services.research import run_topic_poll
        await update_job(db, job_id, status="running", mark_started=True,
                         progress_note=f"Researching topic: {entity_name}")
        try:
            result = await run_topic_poll(db, entity_id, entity_name, model=model)
        except Exception as e:
            logger.exception("Topic research job %s crashed", job_id)
            await update_job(db, job_id, status="failed",
                             error=str(e), mark_completed=True)
            return
        n = result.get("discovered_count", 0)
        note = f"{n} new source(s) on {entity_name}" if n else f"No new sources for {entity_name}"
        await update_job(db, job_id, status="completed",
                         cost_usd=result.get("cost_usd", 0.0),
                         progress_note=note,
                         mark_completed=True)
    await _with_db(db_path, _work)


async def queue_topic_research_job(db, db_path: str, entity_id: int,
                                     entity_name: str, model: str = "sonnet") -> int:
    """Sparse graph-topic research, no article required."""
    job_id = await create_job(
        db,
        job_kind="topic_research",
        content_type="topic",
        title=f"Topic research: {entity_name}",
        params_json=json.dumps({"entity_id": entity_id, "entity_name": entity_name, "model": model}),
    )
    asyncio.create_task(_run_topic_research_job(db_path, job_id, entity_id, entity_name, model))
    return job_id


async def _run_auto_podcast_job(db_path: str, job_id: int,
                                  entity_id: int, entity_name: str, model: str):
    async def _work(db):
        from services.auto_podcast import run_topic_podcast
        await update_job(db, job_id, status="running", mark_started=True,
                         progress_note=f"Briefing on {entity_name}…")
        try:
            result = await run_topic_podcast(db, entity_id, entity_name, model=model)
        except Exception as e:
            logger.exception("Auto-podcast job %s crashed", job_id)
            await update_job(db, job_id, status="failed",
                             error=str(e), mark_completed=True)
            return
        if result.get("success"):
            await update_job(db, job_id, status="completed",
                             result_id=result.get("id"),
                             cost_usd=result.get("cost_usd", 0.0),
                             title=f"Briefing: {entity_name}",
                             progress_note=None,
                             mark_completed=True)
        else:
            await update_job(db, job_id, status="failed",
                             error=result.get("error", "Unknown error"),
                             mark_completed=True)
    await _with_db(db_path, _work)


async def queue_auto_podcast_job(db, db_path: str, entity_id: int,
                                   entity_name: str, model: str = "sonnet") -> int:
    """Sparse graph-driven podcast generation."""
    job_id = await create_job(
        db,
        job_kind="auto_podcast",
        content_type="podcast_script",
        title=f"Auto-podcast: {entity_name}",
        params_json=json.dumps({"entity_id": entity_id, "entity_name": entity_name, "model": model}),
    )
    asyncio.create_task(_run_auto_podcast_job(db_path, job_id, entity_id, entity_name, model))
    return job_id
