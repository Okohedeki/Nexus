"""Recurring scheduler — wakes periodically and runs:
  - per-thread research polls (existing article threads)
  - graph-driven topic research (sparse — every TOPIC_RESEARCH_HOURS)
  - graph-driven auto-podcast (sparse — every AUTO_PODCAST_HOURS)
"""

import asyncio
import logging
import os
import time

import aiosqlite

logger = logging.getLogger(__name__)

# Scheduler tick interval. Cycles below have their own minimum-interval guards.
_TICK_SECONDS = int(os.environ.get("RESEARCH_SCHEDULER_TICK", "900"))  # 15 minutes

# Sparse graph-driven cadences. Each cycle picks at most ONE topic.
_TOPIC_RESEARCH_HOURS = float(os.environ.get("AUTO_TOPIC_RESEARCH_HOURS", "6"))
_AUTO_PODCAST_HOURS   = float(os.environ.get("AUTO_PODCAST_HOURS", "24"))

_last_topic_research_ts = 0.0
_last_auto_podcast_ts = 0.0


async def _tick_threads(db_path: str):
    """Per-article research-thread polls."""
    from services.jobs import queue_research_job
    from services.knowledge_graph import (
        get_generated_content,
        list_due_threads,
    )
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        due = await list_due_threads(conn)
        if not due:
            return
        model = os.environ.get("DEFAULT_MODEL", "sonnet")
        for thread in due:
            gen = await get_generated_content(conn, thread["generated_id"])
            title = (gen or {}).get("title", "") if gen else ""
            try:
                job_id = await queue_research_job(
                    conn, db_path, thread["id"], article_title=title, model=model
                )
                logger.info("Scheduler queued research job %s for thread %s",
                            job_id, thread["id"])
            except Exception as e:
                logger.exception("Failed to queue research job for thread %s: %s",
                                 thread["id"], e)
    finally:
        await conn.close()


async def _tick_topic_research(db_path: str):
    """One sparse graph-topic research cycle (cooldown enforced inside picker)."""
    from services.jobs import queue_topic_research_job
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        from services.graph_intel import pick_topic_for_research
        pick = await pick_topic_for_research(conn, cooldown_hours=72)
        if not pick:
            logger.info("Topic-research: no eligible topic this cycle")
            return
        model = os.environ.get("DEFAULT_MODEL", "sonnet")
        job_id = await queue_topic_research_job(
            conn, db_path, pick["id"], pick["name"], model=model,
        )
        logger.info("Scheduler queued topic-research job %s for entity %s (score=%.2f)",
                    job_id, pick["name"], pick["score"])
    finally:
        await conn.close()


async def _tick_auto_podcast(db_path: str):
    """One sparse graph-driven podcast cycle."""
    from services.jobs import queue_auto_podcast_job
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        from services.graph_intel import pick_topic_for_podcast
        pick = await pick_topic_for_podcast(conn, cooldown_hours=168, min_recent=3)
        if not pick:
            logger.info("Auto-podcast: no fresh topic this cycle")
            return
        model = os.environ.get("DEFAULT_MODEL", "sonnet")
        job_id = await queue_auto_podcast_job(
            conn, db_path, pick["id"], pick["name"], model=model,
        )
        logger.info("Scheduler queued auto-podcast job %s for entity %s (score=%.2f)",
                    job_id, pick["name"], pick["score"])
    finally:
        await conn.close()


async def _tick_once(db_path: str):
    global _last_topic_research_ts, _last_auto_podcast_ts
    now = time.time()
    try:
        await _tick_threads(db_path)
    except Exception:
        logger.exception("threads tick failed")

    if (now - _last_topic_research_ts) >= _TOPIC_RESEARCH_HOURS * 3600:
        try:
            await _tick_topic_research(db_path)
        except Exception:
            logger.exception("topic-research tick failed")
        _last_topic_research_ts = now

    if (now - _last_auto_podcast_ts) >= _AUTO_PODCAST_HOURS * 3600:
        try:
            await _tick_auto_podcast(db_path)
        except Exception:
            logger.exception("auto-podcast tick failed")
        _last_auto_podcast_ts = now


async def _scheduler_loop(db_path: str, stop_event: asyncio.Event):
    logger.info("Research scheduler started (tick=%ss)", _TICK_SECONDS)
    # Initial small delay so startup logs settle
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10)
        return
    except asyncio.TimeoutError:
        pass
    while not stop_event.is_set():
        try:
            await _tick_once(db_path)
        except Exception as e:
            logger.exception("Scheduler tick failed: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_TICK_SECONDS)
        except asyncio.TimeoutError:
            continue
    logger.info("Research scheduler stopped")


_scheduler_task: asyncio.Task | None = None
_scheduler_stop: asyncio.Event | None = None


def start_research_scheduler(db_path: str) -> None:
    """Spawn the recurring scheduler task. Idempotent."""
    global _scheduler_task, _scheduler_stop
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_stop = asyncio.Event()
    _scheduler_task = asyncio.create_task(_scheduler_loop(db_path, _scheduler_stop))


async def stop_research_scheduler() -> None:
    global _scheduler_task, _scheduler_stop
    if _scheduler_stop:
        _scheduler_stop.set()
    if _scheduler_task:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _scheduler_task.cancel()
    _scheduler_task = None
    _scheduler_stop = None
